"""LiveKit voice-agent worker for OutboundAI.

The worker:
  1. Connects to a LiveKit room dispatched by the FastAPI server.
  2. Reads phone number / lead / business context from job metadata.
  3. Dials the lead through the Vobiz SIP trunk (dial-first pattern).
  4. Starts a Gemini Live AgentSession (or pipeline fallback) inside the room.
  5. Optionally records the call to S3 via Egress.
  6. Watches for SIP participant disconnect and tears down cleanly.
"""

import asyncio
import json
import logging
import os
import ssl
from typing import Optional

import certifi
from dotenv import load_dotenv

# Patch SSL with certifi BEFORE any networked imports — fixes "certificate verify failed".
_orig_ssl = ssl.create_default_context


def _certifi_ssl(purpose=ssl.Purpose.SERVER_AUTH, **kwargs):
    if not kwargs.get("cafile") and not kwargs.get("capath") and not kwargs.get("cadata"):
        kwargs["cafile"] = certifi.where()
    return _orig_ssl(purpose, **kwargs)


ssl.create_default_context = _certifi_ssl
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from livekit import agents, api, rtc  # noqa: E402
from livekit.agents import Agent, AgentSession, RoomInputOptions  # noqa: E402

try:
    from livekit.agents import RoomOptions as _RoomOptions  # noqa: F401
    _HAS_ROOM_OPTIONS = True
except ImportError:
    _HAS_ROOM_OPTIONS = False

from livekit.plugins import noise_cancellation, silero  # noqa: E402

from db import init_db, log_error, get_enabled_tools  # noqa: E402
from prompts import build_prompt  # noqa: E402
from tools import AppointmentTools  # noqa: E402

load_dotenv(".env")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound-agent")

SIP_DOMAIN = os.getenv("VOBIZ_SIP_DOMAIN", "")


async def _log(level: str, msg: str, detail: str = "") -> None:
    if level == "info":
        logger.info(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.error(msg)
    try:
        await log_error("agent", msg, detail, level)
    except Exception:
        pass


# NOTE: There is intentionally NO ``load_db_settings_to_env`` here.
# The VPS environment is the single source of truth for credentials.
# Per-call overrides (voice, model, tools, prompt) are passed via room
# metadata and applied locally in the entrypoint without mutating os.environ.


# ── Import Google plugin paths ───────────────────────────────────────────────
_google_realtime = None
_google_beta_realtime = None
_google_llm = None
_google_tts = None

try:
    from livekit.plugins import google as _gp
    try:
        _google_realtime = _gp.realtime.RealtimeModel
        logger.info("Loaded google.realtime.RealtimeModel (stable path)")
    except AttributeError:
        pass
    try:
        _google_beta_realtime = _gp.beta.realtime.RealtimeModel
        logger.info("Loaded google.beta.realtime.RealtimeModel (beta path)")
    except AttributeError:
        pass
    try:
        _google_llm = _gp.LLM
        _google_tts = _gp.TTS
    except AttributeError:
        pass
except ImportError:
    logger.warning("livekit-plugins-google not installed")

_deepgram_stt = None
try:
    from livekit.plugins import deepgram as _dg
    _deepgram_stt = _dg.STT
except ImportError:
    pass


# ── Session factory ──────────────────────────────────────────────────────────

def _build_session(
    tools: list,
    system_prompt: str,
    voice_override: Optional[str] = None,
    model_override: Optional[str] = None,
) -> AgentSession:
    """Build AgentSession with Gemini Live (preferred) or pipeline fallback.

    All three silence-prevention configs are MANDATORY for Gemini Live:
      1. SessionResumptionConfig(transparent=True) — auto-reconnects on timeout
      2. ContextWindowCompressionConfig — prevents freeze when context fills up
      3. RealtimeInputConfig with EndSensitivity.END_SENSITIVITY_LOW + 2s silence

    Credentials come from os.environ. Per-call ``voice_override`` /
    ``model_override`` only affect THIS session — they are never written back
    into os.environ.
    """

    use_realtime = os.getenv("USE_GEMINI_REALTIME", "true").lower() in ("1", "true", "yes")
    api_key = os.getenv("GOOGLE_API_KEY", "")
    model_name = model_override or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
    voice = voice_override or os.getenv("GEMINI_TTS_VOICE", "Aoede")

    realtime_cls = _google_beta_realtime or _google_realtime

    if use_realtime and api_key and realtime_cls is not None:
        try:
            from google.genai import types as _gt

            session_resumption = _gt.SessionResumptionConfig(transparent=True)
            context_compression = _gt.ContextWindowCompressionConfig(
                trigger_tokens=25600,
                sliding_window=_gt.SlidingWindow(target_tokens=12800),
            )
            realtime_input = _gt.RealtimeInputConfig(
                automatic_activity_detection=_gt.AutomaticActivityDetection(
                    end_of_speech_sensitivity=_gt.EndSensitivity.END_SENSITIVITY_HIGH,
                    silence_duration_ms=800,
                    prefix_padding_ms=200,
                ),
            )

            kwargs = dict(
                model=model_name,
                voice=voice,
                api_key=api_key,
                instructions=system_prompt,
                temperature=0.7,
            )
            # Try every spelling the plugin exposes for the silence-prevention configs.
            try:
                rt_model = realtime_cls(
                    **kwargs,
                    session_resumption=session_resumption,
                    context_window_compression=context_compression,
                    realtime_input_config=realtime_input,
                )
            except TypeError:
                try:
                    rt_model = realtime_cls(
                        **kwargs,
                        session_resumption_config=session_resumption,
                        context_window_compression=context_compression,
                        realtime_input_config=realtime_input,
                    )
                except TypeError:
                    logger.warning(
                        "Gemini Live plugin does not accept silence-prevention kwargs; "
                        "falling back to bare RealtimeModel — calls may go silent after ~60s."
                    )
                    rt_model = realtime_cls(**kwargs)

            logger.info("Built Gemini Live session: model=%s, voice=%s", model_name, voice)
            return AgentSession(llm=rt_model, tools=tools)
        except Exception as exc:
            logger.error("Gemini Live init failed (%s) — falling back to pipeline.", exc)

    # ── Pipeline fallback (Deepgram STT + LLM + TTS) ───────────────────────
    if _deepgram_stt is None or _google_llm is None or _google_tts is None:
        raise RuntimeError(
            "Cannot build session: Gemini Live unavailable AND pipeline plugins missing."
        )
    logger.info("Using pipeline fallback (Deepgram STT + Gemini LLM + Gemini TTS).")
    return AgentSession(
        vad=silero.VAD.load(),
        stt=_deepgram_stt(model="nova-2"),
        llm=_google_llm(model="gemini-2.0-flash"),
        tts=_google_tts(voice_name=voice),
        tools=tools,
    )


class OutboundAssistant(Agent):
    """Thin Agent wrapper. Tools are attached to the AgentSession, not here."""

    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions, tools=[])


async def entrypoint(ctx: agents.JobContext) -> None:
    # ── Parse metadata ───────────────────────────────────────────────────────
    phone_number = None
    lead_name = "there"
    business_name = "our company"
    service_type = "our service"
    custom_prompt = None
    voice_override = None
    model_override = None
    tools_override = None

    def _read(meta: dict) -> None:
        nonlocal phone_number, lead_name, business_name, service_type
        nonlocal custom_prompt, voice_override, model_override, tools_override
        phone_number = meta.get("phone_number") or phone_number
        lead_name = meta.get("lead_name") or lead_name
        business_name = meta.get("business_name") or business_name
        service_type = meta.get("service_type") or service_type
        if meta.get("system_prompt"):
            custom_prompt = meta["system_prompt"]
        voice_override = meta.get("voice_override") or voice_override
        model_override = meta.get("model_override") or model_override
        tools_override = meta.get("tools_override") or tools_override

    try:
        if ctx.job and ctx.job.metadata:
            _read(json.loads(ctx.job.metadata))
    except Exception:
        pass
    try:
        if ctx.room and ctx.room.metadata:
            _read(json.loads(ctx.room.metadata))
    except Exception:
        pass

    # Per-call overrides are applied locally inside _build_session —
    # they MUST NOT mutate os.environ (single source of truth = VPS env).

    # Resolve enabled tools (override > setting > all).
    enabled_tools: list = []
    if tools_override:
        try:
            parsed = json.loads(tools_override) if isinstance(tools_override, str) else tools_override
            if isinstance(parsed, list):
                enabled_tools = parsed
        except Exception:
            pass
    if not enabled_tools:
        try:
            enabled_tools = await get_enabled_tools()
        except Exception:
            enabled_tools = []

    # Resolve system prompt: per-call override > saved default > built-in.
    if not custom_prompt:
        try:
            from db import get_setting
            saved = await get_setting("system_prompt", "")
            if saved:
                custom_prompt = saved
        except Exception:
            pass
    system_prompt = build_prompt(
        lead_name=lead_name,
        business_name=business_name,
        service_type=service_type,
        custom_prompt=custom_prompt,
    )

    tool_ctx = AppointmentTools(ctx, phone_number=phone_number, lead_name=lead_name)

    await ctx.connect()
    await _log("info", f"Connected to LiveKit room: {ctx.room.name}")

    # ── Build Gemini Live session early (pre-warm during ring time) ──────────
    active_model = model_override or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
    await _log("info", f"Building AI session — model={active_model}")
    active_tools = tool_ctx.build_tool_list(enabled_tools)
    await _log("info", f"Tools loaded: {[t.__name__ for t in active_tools]}")
    session = _build_session(
        tools=active_tools,
        system_prompt=system_prompt,
        voice_override=voice_override,
        model_override=model_override,
    )

    # NEVER use close_on_disconnect=True with SIP — drops on any audio blip.
    if _HAS_ROOM_OPTIONS:
        from livekit.agents import RoomOptions as _RO
        session_kwargs = dict(
            room=ctx.room,
            agent=OutboundAssistant(instructions=system_prompt),
            room_options=_RO(input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony())),
        )
    else:
        session_kwargs = dict(
            room=ctx.room,
            agent=OutboundAssistant(instructions=system_prompt),
            room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony()),
        )

    # Start session.start() as a background task so the Gemini Live WebSocket
    # warms up during the SIP ring time (typically 5–20 s). By the time the
    # lead picks up, the AI is already initialised and can speak immediately.
    prewarm_task = asyncio.create_task(session.start(**session_kwargs))
    await _log("info", "Gemini Live pre-warm started — dialing now")

    # ── Dial ─────────────────────────────────────────────────────────────────
    if phone_number:
        trunk_id = os.getenv("OUTBOUND_TRUNK_ID")
        if not trunk_id:
            prewarm_task.cancel()
            await _log("error", "OUTBOUND_TRUNK_ID not set — cannot place outbound call")
            ctx.shutdown()
            return
        await _log("info", f"Dialing {phone_number} via SIP trunk {trunk_id}")
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number}",
                    wait_until_answered=True,
                )
            )
        except Exception as exc:
            prewarm_task.cancel()
            await _log("error", f"SIP dial FAILED for {phone_number}: {exc}")
            ctx.shutdown()
            return
        await _log("info", f"Call ANSWERED — {phone_number} picked up")

    # Ensure session is fully started (usually already complete during ring time)
    if not prewarm_task.done():
        await _log("info", "Awaiting session warm-up completion...")
        await prewarm_task
    await _log("info", "AI session live — ready to speak")

    # ── Optional S3 recording ────────────────────────────────────────────────
    if phone_number:
        aws_key = os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID", "")
        aws_secret = os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", "")
        aws_bucket = os.getenv("S3_BUCKET") or os.getenv("AWS_BUCKET_NAME", "")
        s3_endpoint = os.getenv("S3_ENDPOINT_URL") or os.getenv("S3_ENDPOINT", "")
        s3_region = os.getenv("S3_REGION") or os.getenv("AWS_REGION", "ap-northeast-1")
        if aws_key and aws_secret and aws_bucket:
            try:
                recording_path = f"recordings/{ctx.room.name}.ogg"
                egress_req = api.RoomCompositeEgressRequest(
                    room_name=ctx.room.name, audio_only=True,
                    file_outputs=[api.EncodedFileOutput(
                        file_type=api.EncodedFileType.OGG, filepath=recording_path,
                        s3=api.S3Upload(
                            access_key=aws_key, secret=aws_secret,
                            bucket=aws_bucket, region=s3_region, endpoint=s3_endpoint,
                        ),
                    )],
                )
                egress = await ctx.api.egress.start_room_composite_egress(egress_req)
                ep = s3_endpoint.rstrip("/")
                tool_ctx.recording_url = (
                    f"{ep}/{aws_bucket}/{recording_path}" if ep else f"s3://{aws_bucket}/{recording_path}"
                )
                await _log("info", f"Recording started: egress={egress.egress_id}")
            except Exception as exc:
                await _log("warning", f"Recording start failed (non-fatal): {exc}")

    # ── Greeting ─────────────────────────────────────────────────────────────
    # gemini-3.1 and gemini-2.5 native-audio speak autonomously from system prompt.
    # generate_reply() is blocked by the plugin for these models — skip it entirely.
    if "live" in active_model or "native-audio" in active_model:
        await _log("info", "Gemini native-audio: model will greet autonomously from system prompt")
    else:
        greeting = (
            f"The call just connected. Greet the lead and ask if you're speaking with {lead_name}."
            if phone_number else "Greet the caller warmly."
        )
        try:
            await session.generate_reply(instructions=greeting)
        except Exception as gr_exc:
            await _log("warning", f"generate_reply failed: {gr_exc}")

    # ── Keep session alive until SIP participant actually leaves ─────────────
    if phone_number:
        sip_identity = f"sip_{phone_number}"
        disconnect_event = asyncio.Event()

        def _on_participant_disconnected(participant: rtc.RemoteParticipant):
            if participant.identity == sip_identity:
                disconnect_event.set()

        def _on_disconnected():
            disconnect_event.set()

        ctx.room.on("participant_disconnected", _on_participant_disconnected)
        ctx.room.on("disconnected", _on_disconnected)

        try:
            await asyncio.wait_for(disconnect_event.wait(), timeout=3600)
        except asyncio.TimeoutError:
            await _log("warning", "Call reached 1-hour safety timeout — shutting down")

        await _log("info", f"SIP participant disconnected — ending session for {phone_number}")
        try:
            await session.aclose()
        except Exception:
            pass
    else:
        done = asyncio.Event()
        ctx.room.on("disconnected", lambda: done.set())
        try:
            await asyncio.wait_for(done.wait(), timeout=3600)
        except asyncio.TimeoutError:
            pass


if __name__ == "__main__":
    # Single source of truth = VPS environment variables.
    # init_db only verifies Supabase connectivity — it does NOT load env vars.
    init_db()
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="outbound-caller")
    )
