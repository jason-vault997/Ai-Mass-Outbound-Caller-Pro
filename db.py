"""Async Supabase data access layer for OutboundAI.

All persistence (settings, calls, appointments, campaigns, agent profiles,
contact memory, error logs) lives in Supabase. The synchronous helper
``init_db`` is invoked at process start to confirm connectivity.
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

# ---------------------------------------------------------------------------
# DEFAULTS — all loaded from environment variables only.
# Never hardcode real credentials here. Use Coolify env vars or .env file.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "LIVEKIT_URL":             os.getenv("LIVEKIT_URL", ""),
    "LIVEKIT_API_KEY":         os.getenv("LIVEKIT_API_KEY", ""),
    "LIVEKIT_API_SECRET":      os.getenv("LIVEKIT_API_SECRET", ""),
    "GOOGLE_API_KEY":          os.getenv("GOOGLE_API_KEY", ""),
    "GEMINI_MODEL":            os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview"),
    "GEMINI_TTS_VOICE":        os.getenv("GEMINI_TTS_VOICE", "Aoede"),
    "USE_GEMINI_REALTIME":     os.getenv("USE_GEMINI_REALTIME", "true"),
    "VOBIZ_SIP_DOMAIN":        os.getenv("VOBIZ_SIP_DOMAIN", ""),
    "VOBIZ_USERNAME":          os.getenv("VOBIZ_USERNAME", ""),
    "VOBIZ_PASSWORD":          os.getenv("VOBIZ_PASSWORD", ""),
    "VOBIZ_OUTBOUND_NUMBER":   os.getenv("VOBIZ_OUTBOUND_NUMBER", ""),
    "OUTBOUND_TRUNK_ID":       os.getenv("OUTBOUND_TRUNK_ID", ""),
    "DEFAULT_TRANSFER_NUMBER": os.getenv("DEFAULT_TRANSFER_NUMBER", ""),
    "SUPABASE_URL":            os.getenv("SUPABASE_URL", ""),
    "SUPABASE_SERVICE_KEY":    os.getenv("SUPABASE_SERVICE_KEY", ""),
    "DEEPGRAM_API_KEY":        os.getenv("DEEPGRAM_API_KEY", ""),
}


def _default(key: str) -> str:
    return os.getenv(key, DEFAULTS.get(key, ""))


SUPABASE_URL = _default("SUPABASE_URL")
SUPABASE_KEY = _default("SUPABASE_SERVICE_KEY")

# ---------------------------------------------------------------------------
# Single source of truth for environment variables: the VPS / container env.
#
# CREDENTIAL_KEYS are READ-ONLY from the dashboard's perspective. They are
# only resolvable through ``os.environ`` and are never persisted to or
# overridden by Supabase. The Settings tab in the UI shows their
# configured/missing status only.
#
# APP_STATE_KEYS are non-secret runtime tweakables that ARE persisted in the
# Supabase ``settings`` table (so they survive restarts and can be edited
# without redeploying). These are not credentials.
# ---------------------------------------------------------------------------
CREDENTIAL_KEYS = {
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    "GOOGLE_API_KEY", "GEMINI_MODEL", "GEMINI_TTS_VOICE", "USE_GEMINI_REALTIME",
    "VOBIZ_SIP_DOMAIN", "VOBIZ_USERNAME", "VOBIZ_PASSWORD",
    "VOBIZ_OUTBOUND_NUMBER", "OUTBOUND_TRUNK_ID", "DEFAULT_TRANSFER_NUMBER",
    "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
    "DEEPGRAM_API_KEY",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL",
    "S3_REGION", "S3_BUCKET",
    "CALCOM_API_KEY", "CALCOM_EVENT_TYPE_ID", "CALCOM_TIMEZONE",
}

APP_STATE_KEYS = {
    "system_prompt",
    "ENABLED_TOOLS",
}

SENSITIVE_KEYS = {
    "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY",
    "VOBIZ_PASSWORD", "TWILIO_AUTH_TOKEN", "SUPABASE_SERVICE_KEY",
    "AWS_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY", "CALCOM_API_KEY",
    "DEEPGRAM_API_KEY",
}


class CredentialWriteError(RuntimeError):
    """Raised when code tries to persist a credential to Supabase."""



def _sdb():
    from supabase import create_client
    return create_client(_default("SUPABASE_URL"), _default("SUPABASE_SERVICE_KEY"))


async def _adb():
    from supabase._async.client import create_client
    return await create_client(_default("SUPABASE_URL"), _default("SUPABASE_SERVICE_KEY"))


def init_db() -> None:
    url = os.getenv("SUPABASE_URL", SUPABASE_URL)
    key = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_KEY)
    if not url or not key:
        print("⚠️  SUPABASE_URL or SUPABASE_SERVICE_KEY not set.")
        return
    try:
        db = _sdb()
        db.table("settings").select("key").limit(1).execute()
        print("✅ Supabase connected")
    except Exception as exc:
        print(f"⚠️  Supabase connection failed: {exc}")
        print("   Run supabase_schema.sql in your Supabase Dashboard → SQL Editor")


# ── Settings ─────────────────────────────────────────────────────────────────
#
# Read model:
#   • Credentials  → ALWAYS from os.environ (the VPS sets these). Never persisted.
#   • App state    → ONLY from Supabase. Never read from env.
#
# Write model:
#   • Credentials  → REJECTED via CredentialWriteError. Set them on the VPS.
#   • App state    → upserted into Supabase ``settings``.
# ─────────────────────────────────────────────────────────────────────────────

async def get_all_settings() -> dict:
    """Return env-status for credentials + raw value for app-state keys.

    Each entry is ``{"value": str, "configured": bool, "source": "env"|"supabase"}``.
    For SENSITIVE_KEYS the value is masked to an empty string in the response.
    """
    out: dict = {}
    # 1) Credentials — env only, read-only.
    for k in CREDENTIAL_KEYS:
        env_val = os.getenv(k, "")
        masked = "" if k in SENSITIVE_KEYS else env_val
        out[k] = {
            "value": masked,
            "configured": bool(env_val),
            "source": "env",
            "editable": False,
        }
    # 2) App state — Supabase only.
    try:
        db = await _adb()
        result = await db.table("settings").select("key, value").execute()
        rows = {row["key"]: row["value"] for row in (result.data or [])}
    except Exception:
        rows = {}
    for k in APP_STATE_KEYS:
        v = rows.get(k, "")
        out[k] = {
            "value": v,
            "configured": bool(v),
            "source": "supabase",
            "editable": True,
        }
    return out


async def save_settings(data: dict) -> dict:
    """Persist ONLY app-state keys to Supabase.

    Returns ``{"saved": [...], "rejected": [...]}`` so the caller can surface
    a helpful message if someone tries to edit a credential through the UI.
    """
    saved: list = []
    rejected: list = []
    rows: list = []
    updated_at = datetime.now().isoformat()
    for k, v in data.items():
        if v is None or v == "":
            continue
        if k in CREDENTIAL_KEYS:
            rejected.append(k)
            continue
        if k in APP_STATE_KEYS:
            rows.append({"key": k, "value": str(v), "updated_at": updated_at})
            saved.append(k)
        # Unknown keys are silently ignored to keep the API forward-compatible.
    if rows:
        db = await _adb()
        await db.table("settings").upsert(rows, on_conflict="key").execute()
    return {"saved": saved, "rejected": rejected}


async def get_setting(key: str, default: str = "") -> str:
    """Read an APP-STATE setting from Supabase. NEVER use this for credentials.

    For credentials, call ``os.getenv(key, default)`` directly.
    """
    if key in CREDENTIAL_KEYS:
        # Defensive: keep this method honest even if a caller misuses it.
        return os.getenv(key, default)
    try:
        db = await _adb()
        result = await db.table("settings").select("value").eq("key", key).maybe_single().execute()
        if result and result.data:
            return result.data["value"]
    except Exception:
        pass
    return default


async def set_setting(key: str, value: str) -> None:
    """Write an APP-STATE setting to Supabase. Rejects credential writes."""
    if key in CREDENTIAL_KEYS:
        raise CredentialWriteError(
            f"Refusing to persist credential '{key}' to Supabase. "
            f"Set it as an environment variable on your VPS."
        )
    db = await _adb()
    await db.table("settings").upsert(
        {"key": key, "value": value, "updated_at": datetime.now().isoformat()},
        on_conflict="key",
    ).execute()


async def get_enabled_tools() -> list:
    raw = await get_setting("ENABLED_TOOLS", "")
    if not raw:
        return []
    try:
        import json
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception:
        return []


# ── Error logs ────────────────────────────────────────────────────────────────

async def log_error(source: str, message: str, detail: str = "", level: str = "error") -> None:
    try:
        db = await _adb()
        await db.table("error_logs").insert({
            "id": str(uuid.uuid4()),
            "source": source,
            "level": level,
            "message": message[:500],
            "detail": detail[:2000],
            "timestamp": datetime.now().isoformat(),
        }).execute()
    except Exception:
        pass


async def get_errors(limit: int = 100) -> list:
    db = await _adb()
    result = await db.table("error_logs").select("*").order("timestamp", desc=True).limit(limit).execute()
    return result.data or []


async def get_logs(level: Optional[str] = None, source: Optional[str] = None, limit: int = 200) -> list:
    db = await _adb()
    query = db.table("error_logs").select("*").order("timestamp", desc=True).limit(limit)
    if level:
        query = query.eq("level", level)
    if source:
        query = query.eq("source", source)
    result = await query.execute()
    return result.data or []


async def clear_errors() -> None:
    db = await _adb()
    await db.table("error_logs").delete().neq("id", "").execute()


# ── Appointments ──────────────────────────────────────────────────────────────

async def insert_appointment(name: str, phone: str, date: str, time: str, service: str) -> str:
    full_id = str(uuid.uuid4())
    booking_id = full_id[:8].upper()
    db = await _adb()
    await db.table("appointments").insert({
        "id": full_id, "name": name, "phone": phone,
        "date": date, "time": time, "service": service,
        "status": "booked", "created_at": datetime.now().isoformat(),
    }).execute()
    return booking_id


async def check_slot(date: str, time: str) -> bool:
    """Returns True if slot is available (no existing booking)."""
    db = await _adb()
    result = await (
        db.table("appointments").select("id")
        .eq("date", date).eq("time", time).eq("status", "booked")
        .maybe_single().execute()
    )
    return result is None or result.data is None


async def get_next_available(date: str, time: str) -> str:
    try:
        dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    except ValueError:
        dt = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for _ in range(7 * 24):
        dt += timedelta(hours=1)
        if 9 <= dt.hour < 18:
            if await check_slot(dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")):
                return f"{dt.strftime('%Y-%m-%d')} at {dt.strftime('%H:%M')}"
    return "no open slots found in the next 7 days"


async def get_all_appointments(date_filter: Optional[str] = None) -> list:
    db = await _adb()
    query = db.table("appointments").select("*").order("date").order("time")
    if date_filter:
        query = query.eq("date", date_filter)
    result = await query.execute()
    return result.data or []


async def cancel_appointment(appointment_id: str) -> bool:
    db = await _adb()
    result = await (
        db.table("appointments").update({"status": "cancelled"})
        .eq("id", appointment_id).eq("status", "booked").execute()
    )
    return len(result.data or []) > 0


async def get_appointments_by_phone(phone: str) -> list:
    db = await _adb()
    result = await db.table("appointments").select("*").eq("phone", phone).order("date", desc=True).execute()
    return result.data or []


# ── Call logs ─────────────────────────────────────────────────────────────────

async def log_call(
    phone_number: str, lead_name: Optional[str], outcome: str, reason: str,
    duration_seconds: int, recording_url: Optional[str] = None, notes: Optional[str] = None,
) -> None:
    db = await _adb()
    row: dict = {
        "id": str(uuid.uuid4()), "phone_number": phone_number, "lead_name": lead_name,
        "outcome": outcome, "reason": reason, "duration_seconds": duration_seconds,
        "timestamp": datetime.now().isoformat(),
    }
    if recording_url:
        row["recording_url"] = recording_url
    if notes:
        row["notes"] = notes
    await db.table("call_logs").insert(row).execute()


async def get_all_calls(page: int = 1, limit: int = 20) -> list:
    db = await _adb()
    offset = (page - 1) * limit
    result = await db.table("call_logs").select("*").order("timestamp", desc=True).range(offset, offset + limit - 1).execute()
    return result.data or []


async def get_calls_by_phone(phone: str) -> list:
    db = await _adb()
    result = await db.table("call_logs").select("*").eq("phone_number", phone).order("timestamp", desc=True).execute()
    return result.data or []


async def update_call_notes(call_id: str, notes: str) -> bool:
    db = await _adb()
    result = await db.table("call_logs").update({"notes": notes}).eq("id", call_id).execute()
    return len(result.data or []) > 0


async def get_contacts() -> list:
    db = await _adb()
    result = await db.table("call_logs").select("*").order("timestamp", desc=True).execute()
    rows = result.data or []
    contacts: dict = {}
    for row in rows:
        phone = row["phone_number"]
        if phone not in contacts:
            contacts[phone] = {
                "phone_number": phone, "lead_name": row.get("lead_name"),
                "total_calls": 0, "booked": 0,
                "last_call": row["timestamp"], "last_outcome": row.get("outcome"),
            }
        contacts[phone]["total_calls"] += 1
        if row.get("outcome") == "booked":
            contacts[phone]["booked"] += 1
    return sorted(contacts.values(), key=lambda c: c["last_call"], reverse=True)


# ── Stats ─────────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    db = await _adb()
    rows = (await db.table("call_logs").select("outcome, duration_seconds, timestamp").execute()).data or []
    total_calls    = len(rows)
    booked         = sum(1 for r in rows if r.get("outcome") == "booked")
    not_interested = sum(1 for r in rows if r.get("outcome") == "not_interested")
    durations      = [r["duration_seconds"] for r in rows if r.get("duration_seconds")]
    avg_dur        = sum(durations) / len(durations) if durations else 0
    booking_rate   = round((booked / total_calls * 100) if total_calls else 0, 1)
    # Outcomes breakdown
    outcomes: dict = {}
    for r in rows:
        o = r.get("outcome") or "unknown"
        outcomes[o] = outcomes.get(o, 0) + 1
    # Timeline: calls per day last 14 days
    daily: dict = defaultdict(int)
    for r in rows:
        ts = (r.get("timestamp") or "")[:10]
        if ts:
            daily[ts] += 1
    today = datetime.now().date()
    timeline = [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            "count": daily.get((today - timedelta(days=i)).isoformat(), 0),
        }
        for i in range(13, -1, -1)
    ]
    # Avg duration by outcome
    dur_sum: dict = defaultdict(float)
    dur_cnt: dict = defaultdict(int)
    for r in rows:
        o = r.get("outcome") or "unknown"
        sec = r.get("duration_seconds")
        if sec:
            dur_sum[o] += sec
            dur_cnt[o] += 1
    duration_by_outcome = {o: dur_sum[o] / dur_cnt[o] for o in dur_sum}
    return {
        "total_calls": total_calls, "booked": booked, "not_interested": not_interested,
        "avg_duration_seconds": round(avg_dur, 1), "booking_rate_percent": booking_rate,
        "outcomes": outcomes, "timeline": timeline, "duration_by_outcome": duration_by_outcome,
    }


# ── Campaigns ─────────────────────────────────────────────────────────────────

async def create_campaign(
    name: str, contacts_json: str, schedule_type: str = "once",
    schedule_time: str = "09:00", call_delay_seconds: int = 3,
    system_prompt: Optional[str] = None, agent_profile_id: Optional[str] = None,
) -> str:
    campaign_id = str(uuid.uuid4())
    db = await _adb()
    row: dict = {
        "id": campaign_id, "name": name, "status": "active",
        "contacts_json": contacts_json, "schedule_type": schedule_type,
        "schedule_time": schedule_time, "call_delay_seconds": call_delay_seconds,
        "created_at": datetime.now().isoformat(), "total_dispatched": 0, "total_failed": 0,
    }
    if system_prompt:
        row["system_prompt"] = system_prompt
    if agent_profile_id:
        row["agent_profile_id"] = agent_profile_id
    await db.table("campaigns").insert(row).execute()
    return campaign_id


async def get_all_campaigns() -> list:
    db = await _adb()
    result = await db.table("campaigns").select("*").order("created_at", desc=True).execute()
    return result.data or []


async def get_campaign(campaign_id: str) -> Optional[dict]:
    db = await _adb()
    result = await db.table("campaigns").select("*").eq("id", campaign_id).maybe_single().execute()
    return result.data if result else None


async def update_campaign_status(campaign_id: str, status: str) -> bool:
    db = await _adb()
    result = await db.table("campaigns").update({"status": status}).eq("id", campaign_id).execute()
    return len(result.data or []) > 0


async def update_campaign_run_stats(campaign_id: str, dispatched: int, failed: int) -> None:
    db = await _adb()
    await db.table("campaigns").update({
        "last_run_at": datetime.now().isoformat(),
        "total_dispatched": dispatched, "total_failed": failed, "status": "completed",
    }).eq("id", campaign_id).execute()


async def delete_campaign(campaign_id: str) -> bool:
    db = await _adb()
    result = await db.table("campaigns").delete().eq("id", campaign_id).execute()
    return len(result.data or []) > 0


# ── Contact Memory ────────────────────────────────────────────────────────────

async def add_contact_memory(phone: str, insight: str) -> None:
    db = await _adb()
    await db.table("contact_memory").insert({
        "id": str(uuid.uuid4()), "phone_number": phone,
        "insight": insight[:1000], "created_at": datetime.now().isoformat(),
    }).execute()


async def get_contact_memory(phone: str) -> list:
    db = await _adb()
    result = await (
        db.table("contact_memory").select("insight, created_at")
        .eq("phone_number", phone).order("created_at", desc=True).limit(20).execute()
    )
    return result.data or []


async def compress_contact_memory(phone: str, compressed: str) -> None:
    db = await _adb()
    await db.table("contact_memory").delete().eq("phone_number", phone).execute()
    await db.table("contact_memory").insert({
        "id": str(uuid.uuid4()), "phone_number": phone,
        "insight": compressed[:2000], "created_at": datetime.now().isoformat(),
    }).execute()


# ── Agent Profiles ────────────────────────────────────────────────────────────

async def get_all_agent_profiles() -> list:
    db = await _adb()
    result = await db.table("agent_profiles").select("*").order("created_at").execute()
    return result.data or []


async def get_agent_profile(profile_id: str) -> Optional[dict]:
    db = await _adb()
    result = await db.table("agent_profiles").select("*").eq("id", profile_id).maybe_single().execute()
    return result.data if result else None


async def create_agent_profile(
    name: str, voice: str = "Aoede", model: str = "gemini-3.1-flash-live-preview",
    system_prompt: Optional[str] = None, enabled_tools: str = "[]", is_default: bool = False,
    speaks_first: bool = True,
) -> str:
    profile_id = str(uuid.uuid4())
    db = await _adb()
    if is_default:
        await db.table("agent_profiles").update({"is_default": 0}).neq("id", "placeholder").execute()
    await db.table("agent_profiles").insert({
        "id": profile_id, "name": name, "voice": voice, "model": model,
        "system_prompt": system_prompt, "enabled_tools": enabled_tools,
        "is_default": 1 if is_default else 0,
        "speaks_first": 1 if speaks_first else 0,
        "created_at": datetime.now().isoformat(),
    }).execute()
    return profile_id


async def update_agent_profile(profile_id: str, updates: dict) -> bool:
    db = await _adb()
    result = await db.table("agent_profiles").update(updates).eq("id", profile_id).execute()
    return len(result.data or []) > 0


async def delete_agent_profile(profile_id: str) -> bool:
    db = await _adb()
    result = await db.table("agent_profiles").delete().eq("id", profile_id).execute()
    return len(result.data or []) > 0


async def set_default_agent_profile(profile_id: str) -> None:
    db = await _adb()
    await db.table("agent_profiles").update({"is_default": 0}).neq("id", "placeholder").execute()
    await db.table("agent_profiles").update({"is_default": 1}).eq("id", profile_id).execute()
