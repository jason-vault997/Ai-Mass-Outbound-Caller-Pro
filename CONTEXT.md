# OutboundAI — Full Session Checkpoint (v3)

> **For any AI assistant starting a new session:**
> Read this ENTIRE file before touching any code — every section matters.
> This covers the full project from first commit to present: architecture, every file,
> every env var, every bug fixed, every decision made, and exactly what is still pending.
> Update this file at the end of every major session.

---

## 1. Project Overview

An AI-powered **outbound voice calling SaaS platform**. The system automatically phones leads, holds a real voice conversation (greets, qualifies, books appointments), and hangs up — fully autonomous, no human agent required.

**Owner:** Jason. Commercial platform — businesses use it for automated outbound appointment-booking calls.

**GitHub repo:** `https://github.com/jason-vault997/Ai-Mass-Outbound-Caller-Pro.git` (branch: `main`)

---

## 2. Full Technology Stack

| Layer | Technology |
|---|---|
| AI voice model | Google Gemini Live API (`gemini-3.1-flash-live-preview` default; also tested: `gemini-2.5-flash-native-audio-preview-12-2025`) |
| Voice agent orchestration | LiveKit Agents 1.x — `livekit-agents` Python SDK |
| Telephony / SIP | Vobiz SIP trunk connected through LiveKit SIP service |
| Backend API | FastAPI + Uvicorn, port 8000 |
| Database | Supabase (hosted Postgres), accessed async via `supabase-py` |
| Task scheduling | APScheduler `AsyncIOScheduler` for campaigns |
| Frontend | Single HTML file `ui/index.html` — vanilla JS, no build step, served directly by FastAPI |
| Deployment | Docker container managed via Coolify on VPS. Auto-deploys from GitHub `main` |
| Process supervisor | `start.sh` — runs both `server.py` and `agent.py` in one container with POSIX signal handling |
| SSL fix | `certifi` patched onto `ssl.create_default_context` at import time in BOTH `agent.py` AND `server.py` — fixes "certificate verify failed" on VPS. Must stay at the very top, before any LiveKit/Supabase imports |
| Noise cancellation | `livekit-plugins-noise-cancellation` — `BVCTelephony()` applied to session room input options for all SIP calls |
| Recordings | S3-compatible storage via LiveKit Egress (`RoomCompositeEgressRequest`, audio-only OGG). Optional |
| SMS | Twilio REST API via `twilio` SDK. Optional |
| Calendar | Cal.com v1 API via `httpx`. Optional |
| Pipeline fallback | If Gemini Live is unavailable: Deepgram Nova-2 STT + Gemini 2.0-flash LLM + Gemini TTS + Silero VAD |

**THE CARDINAL RULE (enforced in code):**
VPS/Coolify environment variables are the **single source of truth for all credentials**.
- `eff(key)` in `server.py` = `os.getenv(key, "")` — always reads from env, never from DB.
- `CREDENTIAL_KEYS` set in `db.py` — if the UI tries to save any of these to Supabase, they are silently rejected and returned in the `rejected` list.
- `APP_STATE_KEYS` = `{"system_prompt", "ENABLED_TOOLS"}` — the only things editable from the dashboard that get persisted to Supabase.
- The anti-pattern `load_db_settings_to_env()` was explicitly removed from the codebase.

---

## 3. Repository File Map

```
agent.py             — LiveKit worker entrypoint. Dials SIP, builds Gemini Live session
                       (_build_session), greets lead, runs activity watchdog, enforces
                       call duration cap, tears down cleanly (delete_room + shutdown).
server.py            — FastAPI app. 30+ REST endpoints. Call dispatch. Campaign runner.
                       Serves the dashboard HTML. APScheduler lifecycle management.
db.py                — All async Supabase DB helpers (CRUD for every table).
                       Defines CREDENTIAL_KEYS, APP_STATE_KEYS, SENSITIVE_KEYS.
tools.py             — 9 LLM function tools. Memory compression via Gemini 2.0-flash.
prompts.py           — DEFAULT_SYSTEM_PROMPT template + build_prompt() interpolator.
ui/index.html        — Complete single-page dashboard, 12 tabs, served by FastAPI.
supabase_schema.sql  — Full DB schema + migrations. Every statement IF NOT EXISTS — safe to re-run.
.env.example         — Every env var documented with example values.
requirements.txt     — All Python dependencies with minimum version pins.
Dockerfile           — python:3.11-slim. System deps: libgomp1, libglib2.0-0, libsndfile1,
                       ca-certificates, curl. Layer-cached pip install. HEALTHCHECK on /api/health.
start.sh             — POSIX shell process supervisor. Starts uvicorn and agent worker.
                       Traps SIGTERM/SIGINT for clean container shutdown.
CONTEXT.md           — THIS FILE. Full AI session continuity checkpoint.
```

---

## 4. Environment Variables — Complete List (all set in Coolify)

> ⚠️ **CRITICAL:** The Supabase key env var is `SUPABASE_SERVICE_KEY` — NOT `SUPABASE_KEY`.
> This is a common mistake. The code in `db.py` reads `os.getenv("SUPABASE_SERVICE_KEY")`.

### Required — app completely non-functional without these
| Variable | Purpose | Example |
|---|---|---|
| `LIVEKIT_URL` | LiveKit Cloud WSS URL | `wss://myproject.livekit.cloud` |
| `LIVEKIT_API_KEY` | LiveKit API key | `APIxxxxxxxxx` |
| `LIVEKIT_API_SECRET` | LiveKit API secret | `xxxxxxxxxxxxxxxx` |
| `GOOGLE_API_KEY` | Google AI Studio key for Gemini Live | `AIzaSyxxxxxxx` |
| `SUPABASE_URL` | Supabase project URL | `https://xxx.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (NOT anon key) | `eyJhbGci...` |
| `OUTBOUND_TRUNK_ID` | LiveKit SIP trunk ID (starts with `ST_`) | `ST_xxxxxxxxxx` |

### Vobiz SIP setup — required only when creating/recreating the SIP trunk
| Variable | Purpose |
|---|---|
| `VOBIZ_SIP_DOMAIN` | Vobiz SIP server hostname | `xxxxxxxx.sip.vobiz.ai` |
| `VOBIZ_USERNAME` | Vobiz SIP auth username |
| `VOBIZ_PASSWORD` | Vobiz SIP auth password |
| `VOBIZ_OUTBOUND_NUMBER` | Your outbound caller ID in E.164 | `+919876543210` |
| `DEFAULT_TRANSFER_NUMBER` | Phone/SIP number to transfer calls to a human | `+919876543210` |

### Optional — features degrade gracefully if missing
| Variable | Default | What it enables |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.1-flash-live-preview` | Switch Gemini model |
| `GEMINI_TTS_VOICE` | `Aoede` | Switch TTS voice |
| `USE_GEMINI_REALTIME` | `true` | Set `false` to force pipeline fallback |
| `MAX_CALL_DURATION_SECONDS` | `270` | Hard call cap (4m30s). Set in Coolify for visibility |
| `DEEPGRAM_API_KEY` | — | Pipeline fallback STT (Deepgram Nova-2) |
| `TWILIO_ACCOUNT_SID` | — | SMS confirmations |
| `TWILIO_AUTH_TOKEN` | — | SMS confirmations |
| `TWILIO_FROM_NUMBER` | — | SMS confirmations |
| `CALCOM_API_KEY` | — | Cal.com calendar sync |
| `CALCOM_EVENT_TYPE_ID` | — | Cal.com event type to book |
| `CALCOM_TIMEZONE` | `Asia/Kolkata` | Cal.com timezone |
| `S3_ACCESS_KEY_ID` | — | Call recording to S3 |
| `S3_SECRET_ACCESS_KEY` | — | Call recording to S3 |
| `S3_BUCKET` | — | S3 bucket name |
| `S3_ENDPOINT_URL` | — | S3 endpoint (for Supabase Storage or custom S3) |
| `S3_REGION` | `ap-northeast-1` | S3 region |
| `HOST` | `0.0.0.0` | Server bind address — override for Coolify proxy compat |
| `PORT` | `8000` | Server bind port — override for Coolify proxy compat |

---

## 5. Database Schema (Supabase / Postgres)

All tables have `DISABLE ROW LEVEL SECURITY`. All IDs are UUID strings stored as TEXT. All timestamps are ISO strings. Run `supabase_schema.sql` once to initialize.

### `agent_profiles`
AI persona configurations. One profile can be set as default.
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
voice TEXT NOT NULL DEFAULT 'Aoede',
model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-live-preview',
system_prompt TEXT,            -- NULL = use global prompt from settings table
enabled_tools TEXT DEFAULT '[]', -- JSON array of tool names. '[]' or NULL = all 9 enabled
is_default INTEGER DEFAULT 0,
speaks_first INTEGER DEFAULT 1,  -- 1 = AI greets immediately on connect, 0 = wait for user
created_at TEXT NOT NULL
```
> **Migration for existing installs (run once in Supabase SQL Editor):**
> `ALTER TABLE agent_profiles ADD COLUMN IF NOT EXISTS speaks_first INTEGER DEFAULT 1;`
> NULL in this column is treated as 1 (speaks first) for backwards-compat.

### `campaigns`
Bulk outbound call campaigns.
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'active',    -- active | paused | completed
contacts_json TEXT NOT NULL DEFAULT '[]', -- JSON array of contact objects (see format below)
schedule_type TEXT NOT NULL DEFAULT 'once', -- once | daily | weekdays
schedule_time TEXT DEFAULT '09:00',       -- HH:MM in 24h, used for daily/weekdays
call_delay_seconds INTEGER DEFAULT 3,     -- seconds to wait between each call in batch
system_prompt TEXT,                        -- per-campaign override, or NULL
created_at TEXT NOT NULL,
last_run_at TEXT,
total_dispatched INTEGER DEFAULT 0,
total_failed INTEGER DEFAULT 0,
agent_profile_id TEXT    -- nullable FK to agent_profiles.id
```

**Contact JSON format** (what goes inside `contacts_json`):
```json
[
  {
    "phone": "+919876543210",
    "lead_name": "Rahul Sharma",
    "business_name": "Sunshine Clinic",
    "service_type": "dental checkup"
  }
]
```
Phone must be in E.164 format (`+` then country code then number). Campaigns with invalid phone numbers skip that contact and count it as failed.

### `call_logs`
One row per call, written by the `end_call` tool.
```sql
id TEXT PRIMARY KEY,
phone_number TEXT NOT NULL,
lead_name TEXT,
outcome TEXT,        -- booked | not_interested | wrong_number | voicemail | no_answer | callback_requested
reason TEXT,         -- brief human-readable reason
duration_seconds INTEGER,
timestamp TEXT NOT NULL,
recording_url TEXT,  -- S3 URL if recording was enabled for this call
notes TEXT           -- editable post-call from the dashboard
```

### `appointments`
Written by `book_appointment` tool. Can also be cancelled from dashboard.
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
phone TEXT NOT NULL,
date TEXT NOT NULL,   -- YYYY-MM-DD
time TEXT NOT NULL,   -- HH:MM (24h)
service TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'booked',  -- booked | cancelled
created_at TEXT NOT NULL,
calcom_booking_uid TEXT   -- Cal.com UID if also synced via book_calcom tool
```

### `contact_memory`
Per-phone-number AI memory that persists across all future calls.
```sql
id TEXT PRIMARY KEY,
phone_number TEXT NOT NULL,
insight TEXT NOT NULL,
created_at TEXT NOT NULL
```
Index: `CREATE INDEX IF NOT EXISTS idx_contact_memory_phone ON contact_memory (phone_number)`

Auto-compression: when a contact accumulates ≥5 memory entries, `tools.py` fires `_compress_memories()` as a background task. This calls Gemini 2.0-flash to summarize all entries into 3–5 concise bullets, then replaces them in the DB via `compress_contact_memory()`. Requires `GOOGLE_API_KEY`.

### `settings`
Key-value store for non-credential app state.
```sql
key TEXT PRIMARY KEY,
value TEXT NOT NULL,
updated_at TEXT NOT NULL
```
Only two keys are currently used: `system_prompt` (global default prompt override) and `ENABLED_TOOLS` (legacy — superseded by per-profile enabled_tools).

### `error_logs`
Structured log from both agent and server. Streamed live in dashboard.
```sql
id TEXT PRIMARY KEY,
source TEXT NOT NULL,   -- 'agent' | 'server'
level TEXT NOT NULL DEFAULT 'error',  -- 'info' | 'warning' | 'error'
message TEXT NOT NULL,
detail TEXT,
timestamp TEXT NOT NULL
```

---

## 6. How a Call Works (End-to-End)

### Single call (from dashboard):
1. User clicks "Call" → POST `/api/call` with `{phone, lead_name, business_name, service_type, agent_profile_id}`.
2. `server.py` looks up the agent profile, resolves `voice`, `model`, `enabled_tools`, `system_prompt`, `speaks_first`.
3. Server creates LiveKit room (`call-{phone}-{random}`) and dispatches agent job with all context as JSON metadata.
4. `agent.py` `entrypoint()` wakes up, parses metadata via `_read(meta)`.
5. Resolves enabled tools: profile override → Supabase setting → all tools.
6. Resolves system prompt: per-call override → profile → Supabase setting → `build_prompt()` with defaults.
7. Calls `ctx.api.sip.create_sip_participant(wait_until_answered=True)` → **dials the phone** and blocks until answered.
8. If no answer / call rejected: logs error and calls `ctx.shutdown()` — no AI session started.
9. On answer: calls `_build_session()` → creates `AgentSession` with Gemini Live + all 3 silence-prevention configs.
10. `session.start()` with `BVCTelephony()` noise cancellation → AI connected to audio stream.
11. Optionally starts S3 Egress recording.
12. **Greeting block**: if `agent_speaks_first=True` (default), tries `session.generate_reply()` → falls back to `session.say()`.
13. Conversation runs. AI calls tools as needed.
14. **Watchdog** runs as background task. Any session event resets idle timer. >20s idle → recovery prompt.
15. Call ends via: (a) AI calls `end_call` tool → `ctx.room.disconnect()`, (b) human hangs up → `participant_disconnected` fires, or (c) `MAX_CALL_DURATION_SECONDS` timer expires.
16. **Teardown**: `watchdog_task.cancel()` → `session.aclose()` → `ctx.api.room.delete_room()` → `ctx.shutdown()`.
    - `delete_room()` is critical — without it, the LiveKit room stays alive after SIP disconnect, Egress keeps recording, Gemini Live keeps billing.

### Campaign (bulk outbound):
- `schedule_type="once"` → `asyncio.create_task(_run_campaign())` fires immediately.
- `schedule_type="daily"` or `"weekdays"` → APScheduler `CronTrigger` fires at `schedule_time` each day/weekday.
- `_run_campaign()` iterates contacts, calls `_dispatch_one()` per contact, waits `call_delay_seconds` between each.
- `_dispatch_one()` does the same room-create + agent-dispatch as single calls, but using profile from campaign's `agent_profile_id`.
- On server startup, `_reschedule_all_campaigns()` reloads any active daily/weekday campaigns back into APScheduler.

### Vobiz SIP trunk setup (one-time per LiveKit project):
1. Set all Vobiz env vars (`VOBIZ_SIP_DOMAIN`, `VOBIZ_USERNAME`, `VOBIZ_PASSWORD`, `VOBIZ_OUTBOUND_NUMBER`) in Coolify.
2. POST `/api/setup/trunk` from the dashboard → creates trunk in LiveKit → returns `trunk_id` (format: `ST_xxxx`).
3. Add that `trunk_id` as `OUTBOUND_TRUNK_ID` env var in Coolify and redeploy.
4. Verify: GET `/api/setup/trunks` → shows all trunks, which one is current, and a diagnosis message.

---

## 7. The 9 AI Tools (tools.py)

All live in `class AppointmentTools(llm.ToolContext)`. Enabled/disabled per agent profile via `enabled_tools` JSON array. Empty `[]` or NULL = all tools enabled. `build_tool_list(enabled)` filters by name from the full method list.

| Tool | Args | What it does | Notes |
|---|---|---|---|
| `check_availability` | `date` (YYYY-MM-DD), `time` (HH:MM) | Checks `appointments` table for conflicts. Returns `"available"` or `"unavailable: next available slot is ..."` | Call BEFORE confirming any slot |
| `book_appointment` | `name, phone, date, time, service` | Inserts into `appointments` table | Call only AFTER lead verbally confirms |
| `end_call` | `outcome, reason=""` | Logs call to `call_logs`, calls `ctx.room.disconnect()` | ALWAYS call at call end. outcome: booked/not_interested/wrong_number/voicemail/no_answer/callback_requested |
| `transfer_to_human` | `reason` | SIP REFER to `DEFAULT_TRANSFER_NUMBER`. Formats as `sip:number@VOBIZ_SIP_DOMAIN` | Returns failure message if no transfer number configured |
| `send_sms_confirmation` | `phone, message` | Sends SMS via Twilio. Silently skips if Twilio not configured | Non-blocking — booking is already confirmed |
| `lookup_contact` | `phone` | Retrieves call history (last 5) + appointments (last 3) + memory notes (last 10) from Supabase | Call at START of every call |
| `remember_details` | `insight` | Saves to `contact_memory`. Triggers `_compress_memories()` if ≥5 entries exist | Use freely throughout call |
| `book_calcom` | `name, email, date, start_time, notes=""` | Books in Cal.com via v1 API | Requires `CALCOM_API_KEY` + `CALCOM_EVENT_TYPE_ID` |
| `cancel_calcom` | `booking_uid, reason=""` | Cancels Cal.com booking by UID | Requires `CALCOM_API_KEY` |

**Memory auto-compression:** When `remember_details` is called and the contact now has ≥5 memory entries, `_compress_memories()` runs in background via `asyncio.create_task()`. It calls `gemini-2.0-flash` to summarize all entries into 3-5 bullets, then calls `compress_contact_memory()` which deletes the raw entries and inserts the compressed summary.

---

## 8. The AI Persona & System Prompt (prompts.py)

Default persona: **"Priya"** — warm, professional appointment-booking assistant.

`DEFAULT_SYSTEM_PROMPT` key instructions:
- **SPEAK FIRST** immediately on call connect: "Hi, am I speaking with {lead_name}?"
- 6-step call flow: confirm identity → introduce → qualify → find slot → book → close
- Identity check: wrong person → `end_call(wrong_number)`, voicemail → leave message then `end_call(voicemail)`, silence 5s → `end_call(no_answer)`
- Objection handling: "not interested" (ask once more → `end_call`), "stop calling" → `end_call`, "transfer to human" → `transfer_to_human`, "are you a bot?" → deflect warmly
- Style: max 1–2 short sentences per turn, no filler openers ("Certainly!", "Of course!"), no "As an AI"
- Tool rules: `lookup_contact` at call start before speaking, `check_availability` ALWAYS before confirming slot, `end_call` ALWAYS at call end

`build_prompt(lead_name, business_name, service_type, custom_prompt=None)`:
- If `custom_prompt` provided: uses it as the template (entirely replaces default).
- Otherwise: uses `DEFAULT_SYSTEM_PROMPT`.
- Calls `.format(lead_name=..., business_name=..., service_type=...)` on whichever template.
- If the template has no `{...}` placeholders (e.g. a raw custom prompt), the `KeyError` is caught and the template is returned as-is.

---

## 9. The 5 Production Issues — Full History

### Issue 1 — AI never started the conversation ✅ FIXED — Commit `7b68147`

**Root cause:** Original code had this block:
```python
if "3.1" in active_model or "2.5" in active_model:
    # skip generate_reply() — assumes model speaks from system prompt
```
Gemini native-audio models do NOT speak autonomously on call connect. The AI sat silent waiting for the human, who also waited since outbound callers are expected to speak first. Complete deadlock — dead air from second 0.

**Fix:** Removed model-name check. Replaced with a robust try→fallback flow:
```python
if not agent_speaks_first:
    pass  # wait for user (inbound-style)
else:
    try:
        await session.generate_reply(instructions="The call just connected. Speak immediately. Open with: 'Hi, am I speaking with {lead_name}?'")
    except Exception:
        await session.say("Hi, am I speaking with {lead_name}?", allow_interruptions=True)
```
`generate_reply()` produces the most natural result. If the plugin blocks it for this model, `session.say()` is the guaranteed fallback.

**New toggle added simultaneously:** `speaks_first` on agent profiles:
- DB: `speaks_first INTEGER DEFAULT 1` (migration: `ALTER TABLE agent_profiles ADD COLUMN IF NOT EXISTS speaks_first INTEGER DEFAULT 1`)
- Dashboard: "AI speaks first" checkbox in agent profile editor, checked by default
- API: `speaks_first: bool = True` in `AgentProfileRequest` in `server.py`
- DB storage: 1/0 integer, read as `profile.get("speaks_first") != 0` (NULL → True for backwards-compat)
- Dispatch: `metadata["agent_speaks_first"] = bool(effective_speaks_first)` in job metadata
- Agent: `_read(meta)` parses it, `if "agent_speaks_first" in meta: agent_speaks_first = bool(...)`
- Use case for False: inbound calls where you want the human to speak first

**Files changed:** `agent.py`, `server.py`, `db.py`, `ui/index.html`, `supabase_schema.sql`

---

### Issue 2 — AI constantly interrupted by phone noise ✅ FIXED — Commit `fa38d6b`

**Root cause:** Gemini Live's `AutomaticActivityDetection` defaults to `START_SENSITIVITY_HIGH`. Any phone tap, background noise, breathing, or brief ambient sound triggered a false "user is speaking" event, interrupting the AI mid-sentence every few words.

**Fix:** One line added to `_build_session()` inside `_gt.AutomaticActivityDetection(...)`:
```python
start_of_speech_sensitivity=_gt.StartSensitivity.START_SENSITIVITY_LOW,  # added
end_of_speech_sensitivity=_gt.EndSensitivity.END_SENSITIVITY_LOW,        # was already there
silence_duration_ms=2000,
prefix_padding_ms=200,
```
`START_SENSITIVITY_LOW` requires a clearer, longer onset before classifying sound as speech. Real intentional human speech still triggers it. Taps, clicks, breathing don't.

**Files changed:** `agent.py` only — 4 lines (1 new kwarg + 3 comment lines)

---

### Issue 3 — AI never recovers from dead air ✅ FIXED — Commit `ff27841`

**Root cause:** After a false interrupt (Issue 2), Gemini Live VAD entered "waiting for user to finish speaking" state. The noise event was too brief to cross the END_SENSITIVITY_LOW threshold, so the AI sat frozen waiting forever. Dead air. Call meter kept running. Previously the safety timeout was 3600s (1 hour) — catastrophic cost risk.

**Fix:** Three-layer defence added to `agent.py` after the greeting block:

**Layer 1 — Watchdog (dead-air recovery):**
```python
activity_state = {"last": time.time()}

for evt in ("user_speech_committed", "agent_speech_committed", "user_state_changed",
            "agent_state_changed", "speech_created", "function_tool_called",
            "user_input_transcribed", "conversation_item_added"):
    try: session.on(evt, _bump_activity)
    except: pass

async def _watchdog():
    while not disconnect_event.is_set():
        await asyncio.sleep(5)
        if time.time() - activity_state["last"] > 20:
            try:
                await session.generate_reply(instructions="Say: 'Hello, are you still there?'")
            except:
                await session.say("Hello, are you still there?", allow_interruptions=True)
            _bump_activity()
```
Polls every 5s. Any session event resets the idle timer. 20s of silence → inject recovery prompt. If both paths fail, logs error (call may still recover if user speaks).

**Layer 2 — Hard duration cap (cost protection):**
```python
max_call_duration = int(os.getenv("MAX_CALL_DURATION_SECONDS", "270"))
await asyncio.wait_for(disconnect_event.wait(), timeout=max_call_duration)
```
**270 seconds = 4 minutes 30 seconds.** Jason explicitly requested this (not 5 min, not 8 min). On timeout, force-sets disconnect, watchdog gets cancelled, teardown begins. Even if AI never calls `end_call`.

**Layer 3 — Clean room teardown (stops billing):**
```python
watchdog_task.cancel()
await session.aclose()
await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
ctx.shutdown()
```
`delete_room()` was missing before. Without it: LiveKit room stayed alive after SIP disconnect, Egress kept recording, Gemini Live WebSocket kept billing. `ctx.shutdown()` was also missing, leaving zombie worker processes.

**Files changed:** `agent.py` (entire disconnect/teardown block), `.env.example` (added `MAX_CALL_DURATION_SECONDS`)

---

### Issue 4 — Tool toggles uncertain ⏳ PENDING VERIFICATION

**Status:** Code not changed. The mechanism exists and looks correct, but not yet verified with a real restricted-profile test call.

How it works:
- `enabled_tools` on `agent_profiles` = JSON string like `'["end_call","book_appointment"]'`
- Empty string `'[]'` or NULL = all 9 tools enabled
- `AppointmentTools.build_tool_list(enabled_list)` filters the full 9-method list by name match
- Passed to `_build_session(tools=active_tools)`
- Agent logs: `[INFO] Tools loaded: ['end_call', 'book_appointment']`

**To verify:** Create agent profile with `enabled_tools = ["end_call"]`, run a test call, confirm log shows only `['end_call']`, try to book an appointment during the call — AI should say it can't.

---

### Issue 5 — Call start latency ⏳ DEFERRED

**Status:** Not addressed. Deferred until Issues 1–3 verified in production.

**The problem:** Delay between human picking up and AI greeting. Causes an awkward silence that may make humans hang up.

**Likely causes:** Gemini Live WebSocket negotiation happens after `wait_until_answered` resolves, SIP audio codec negotiation, Gemini model warm-up.

**Ideas for future session:** Pre-warm `AgentSession` before calling `wait_until_answered`, investigate session resumption for faster reconnect, look at whether `start.sh` can pre-heat the plugin module.

---

## 10. REST API Endpoint Map (server.py)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Serves `ui/index.html` dashboard |
| GET | `/api/health` | Liveness check — returns `{"status": "ok"}` |
| GET | `/api/env-check` | Deployment verification. Shows all env vars configured/missing. Sensitive values masked |
| POST | `/api/call` | Dispatch a single outbound call |
| GET | `/api/calls` | List call logs (paginated: `?page=1&limit=20`) |
| PATCH | `/api/calls/{id}/notes` | Update notes on a call log |
| GET | `/api/stats` | Dashboard summary stats |
| GET | `/api/appointments` | List appointments (`?date=YYYY-MM-DD` filter) |
| DELETE | `/api/appointments/{id}` | Cancel an appointment |
| GET | `/api/prompt` | Get current system prompt (custom or default) |
| POST | `/api/prompt` | Save custom system prompt |
| DELETE | `/api/prompt` | Reset to default prompt |
| GET | `/api/settings` | Get all settings (credentials: masked, app-state: raw) |
| POST | `/api/settings` | Save app-state settings (credentials silently rejected) |
| POST | `/api/setup/trunk` | Create SIP outbound trunk in LiveKit (uses Vobiz env vars) |
| GET | `/api/setup/trunks` | List all LiveKit SIP trunks + diagnosis of current `OUTBOUND_TRUNK_ID` |
| GET | `/api/logs` | Get error/info logs (`?limit=200&level=info&source=agent`) |
| DELETE | `/api/logs` | Clear all logs |
| GET | `/api/crm` | List all contacts (from `contact_memory` + `call_logs`) |
| GET | `/api/crm/calls` | Get calls by phone (`?phone=+91...`) |
| GET | `/api/agent-profiles` | List all agent profiles |
| POST | `/api/agent-profiles` | Create agent profile |
| GET | `/api/agent-profiles/{id}` | Get single profile |
| PUT | `/api/agent-profiles/{id}` | Update profile |
| DELETE | `/api/agent-profiles/{id}` | Delete profile |
| POST | `/api/agent-profiles/{id}/set-default` | Set as default |
| POST | `/api/campaigns` | Create campaign (fires immediately if `once`, schedules if `daily`/`weekdays`) |
| GET | `/api/campaigns` | List all campaigns |
| DELETE | `/api/campaigns/{id}` | Delete campaign + remove APScheduler job |
| POST | `/api/campaigns/{id}/run` | Manually trigger campaign now |
| PATCH | `/api/campaigns/{id}/status` | Update status: `active`/`paused`/`completed` |

---

## 11. Dashboard (ui/index.html) — 12 Tabs

Single-page app, vanilla JS. No build step. Served directly by FastAPI from `ui/index.html`.

| Tab | What's in it |
|---|---|
| **Dashboard** | Stats cards (total calls, booked, appointments), recent call log |
| **Make a Call** | Single call form: phone, lead name, business, service, agent profile picker |
| **Campaigns** | Create bulk campaign (contacts JSON, schedule, delay, profile). List + run/delete/pause |
| **Agent Profiles** | Create/edit profiles: name, voice, model, enabled tools, system prompt, speaks_first, is_default |
| **Prompt** | Edit global system prompt (with character count, reset to default) |
| **Appointments** | View/filter/cancel booked appointments |
| **Call Logs** | Paginated call history with outcome, duration, notes editor, recording link |
| **CRM** | Contact list with memory notes, call history per phone number |
| **Settings** | Environment variable status (configured/missing), app-state editable fields |
| **Setup / SIP** | Create/verify SIP trunk — lists all trunks, shows diagnosis, create trunk button |
| **Logs** | Live log stream from `error_logs` table. Filter by level/source. Clear button |
| **API Docs** | Links to LiveKit, Supabase, Google AI docs |

---

## 12. `_build_session()` — Critical Implementation Details

```python
def _build_session(tools, system_prompt, voice_override=None, model_override=None) -> AgentSession:
```

**Primary path (Gemini Live):** Requires `USE_GEMINI_REALTIME=true` (default) + `GOOGLE_API_KEY` + the Google plugin installed.

**3 silence-prevention configs — ALL MANDATORY:**
1. `SessionResumptionConfig(transparent=True)` — auto-reconnects Gemini Live WebSocket on idle timeout without losing conversation history. Without this, calls go silent after ~60s.
2. `ContextWindowCompressionConfig(trigger_tokens=25600, sliding_window=SlidingWindow(target_tokens=12800))` — compresses context when it fills up. Without this, Gemini freezes on long calls.
3. `RealtimeInputConfig(AutomaticActivityDetection(start_of_speech_sensitivity=LOW, end_of_speech_sensitivity=LOW, silence_duration_ms=2000, prefix_padding_ms=200))` — VAD tuning. Without `START_SENSITIVITY_LOW`, any phone noise interrupts the AI (Issue 2).

**Plugin API compatibility:** The plugin has two different parameter name spellings depending on version. The code tries both in nested try/except:
- First try: `session_resumption=...`
- Second try: `session_resumption_config=...`
- Final fallback: bare `realtime_cls(**kwargs)` with a warning that silence-prevention is degraded.

**Fallback pipeline (if Gemini Live unavailable):**
`Deepgram Nova-2 STT + Gemini 2.0-flash LLM + Gemini TTS + Silero VAD`. Requires `DEEPGRAM_API_KEY`. `generate_reply()` works normally in pipeline mode.

---

## 13. `start.sh` — How Both Processes Run

```sh
uvicorn server:app --host "${HOST}" --port "${PORT}" &
SERVER_PID=$!
sleep 2
python -u agent.py start &
AGENT_PID=$!
# POSIX poll loop — if either exits, kills the other and exits container
```

Both run in the same Docker container. `start.sh` is the `CMD` in `Dockerfile`. Traps `SIGTERM` and `SIGINT` to call `cleanup()` which sends SIGTERM to both processes. If either process crashes, the container exits (so Coolify restarts it).

The `sleep 2` between starting server and agent ensures the Supabase `init_db()` health check in `server.py` runs first. Agent worker uses the same env vars.

---

## 14. Full Project Chronology (Session History)

**Phase 0 — Feb 7, 2026 (commits `92d3798`, `4b51d37`):**
Original system by Shreyas Raj. Used OpenAI pipeline (not Gemini Live). Dashboard was Next.js (`/dashboard` folder). Tools: basic call dispatch. This codebase was the starting point that got replaced.

**Phase 1 — May 17, 2026 (commit `9311b80`):**
Complete rebuild — the entire `dashboard/` Next.js app was replaced with `ui/index.html`. OpenAI/Deepgram/Groq pipeline replaced with Gemini Live. FastAPI server built from scratch with 30+ endpoints. Supabase data layer added. All 9 tools added. APScheduler added. Agent profiles added. S3 Egress recordings added. SSL certifi patch added. 3 silence-prevention configs added. `Dockerfile` hardened. `start.sh` process supervisor added.

**Phase 1b — May 17, 2026 (commit `69bd9d1`):**
`HOST` and `PORT` env vars made overridable in `Dockerfile` and `start.sh`. Reason: Coolify uses a reverse proxy and may set `PORT` dynamically. Without this, the container always bound to 8000 regardless of Coolify's port mapping, causing health check failures.

**Phase 1c — May 17, 2026 (commit `27496ca`):**
Added `GET /api/setup/trunks` endpoint and a corresponding diagnostic tab in the dashboard. Reason: after first deployment attempt, calls were failing because `OUTBOUND_TRUNK_ID` was wrong or pointing to a non-existent trunk. This endpoint tells you exactly what's configured vs what LiveKit actually has, and prints a human-readable diagnosis.

**Phase 2 — May 18, 2026 (commits `7b68147`, `fa38d6b`, `ff27841`):**
5 production issues identified after first real test calls. Issues 1 (no greeting), 2 (mic sensitivity), 3 (dead air) fixed in separate commits. Issues 4 and 5 deferred. Hard call cap set to 270s (4m30s) on Jason's explicit request.

**Phase 3 — May 19, 2026 (commits `469b7ab`, `6ac6a15`):**
SQL migration question answered. CONTEXT.md created for session continuity. Rebuilt comprehensively (this file, v3).

---

## 15. Git History (all commits, newest first)

| Commit | Date | Description |
|---|---|---|
| `6ac6a15` | May 19 | docs: full session checkpoint rebuild v3 |
| `469b7ab` | May 19 | docs: initial CONTEXT.md |
| `ff27841` | May 18 | feat(issue-3): watchdog + 4m30s cap + clean teardown |
| `fa38d6b` | May 18 | fix(issue-2): START_SENSITIVITY_LOW VAD fix |
| `7b68147` | May 18 | feat(issue-1): AI greets first + speaks_first toggle |
| `27496ca` | May 17 | feat: /api/setup/trunks diagnostic endpoint |
| `69bd9d1` | May 17 | feat: HOST/PORT env-overridable for proxy compat |
| `9311b80` | May 17 | feat: complete rebuild as OutboundAI SaaS |
| `4b51d37` | Feb 7 | Add core agent code (original OpenAI system) |
| `92d3798` | Feb 7 | first commit (README only) |

**Stable baseline before Issues 1–3 fixes:** `27496ca`

---

## 16. Rollback Instructions

Every issue is a separate, self-contained commit for clean individual revert.

```bash
# Revert just Issue 3 (watchdog/cap/teardown)
git revert ff27841 && git push

# Revert just Issue 2 (VAD sensitivity)
git revert fa38d6b && git push

# Revert just Issue 1 (greeting/speaks_first)
git revert 7b68147 && git push

# Revert all three at once (safe — creates revert commits)
git revert --no-commit ff27841 fa38d6b 7b68147 && git commit -m "revert issues 1-3 fixes" && git push

# Nuclear reset (DESTRUCTIVE — rewrites history)
git reset --hard 27496ca && git push --force-with-lease origin main
```

---

## 17. How to Run

### Locally (dev)
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in all required values
uvicorn server:app --host 0.0.0.0 --port 8000 &
python agent.py start
```
Dashboard: http://localhost:8000

### Production via Docker / Coolify
```
Dockerfile CMD = ["sh", "start.sh"]
start.sh starts uvicorn (server) and python agent.py start (worker) in same container.
Coolify auto-deploys on git push to main.
Env vars managed in Coolify UI — never in code.
```

### Supabase one-time setup
1. Run entire `supabase_schema.sql` in Supabase → SQL Editor.
2. For existing installs missing `speaks_first` column: `ALTER TABLE agent_profiles ADD COLUMN IF NOT EXISTS speaks_first INTEGER DEFAULT 1;`

### First SIP trunk setup
1. Set `VOBIZ_*` env vars in Coolify, redeploy.
2. POST `/api/setup/trunk` from dashboard → note the `trunk_id`.
3. Set `OUTBOUND_TRUNK_ID=ST_xxxxx` in Coolify, redeploy.
4. Verify: GET `/api/setup/trunks` → should say "OK — current trunk is valid and exists."

---

## 18. User Preferences & Hard Decisions (do not change without asking Jason)

- **No experimental changes.** Every fix must be surgical, reversible, and non-destabilizing.
- **No changes to `tools.py` or `prompts.py`** until Issues 1–3 are verified working in production.
- **4 minutes 30 seconds** (270s) is the hard call cap. Jason specifically said this. Not 5 min, not 8 min.
- **Separate commits per issue.** Never bundle unrelated fixes into one commit.
- **`speaks_first` defaults to True.** NULL from old rows = treats as True (backwards-compat).
- **Credentials live ONLY in Coolify env vars.** Never in DB, never in code. `eff(key)` = `os.getenv(key)`.
- **Python dependencies:** Do not upgrade `livekit-agents` or `livekit-plugins-google` without testing — plugin API changes have broken things before (the `session_resumption` vs `session_resumption_config` issue).

---

## 19. What to Do Next Session

**Step 1 — Verify Issues 1–3 in production:**
Run a test call. Check `error_logs` or Coolify container logs for:
- `[INFO] Greeting triggered via generate_reply` ← Issue 1 working
- OR `[INFO] Greeting triggered via session.say()` ← fallback path worked
- Stay silent 25s → `[WARNING] Dead-air detected` + AI says "Hello, are you still there?" ← Issue 3 working
- Speak over the AI → it should NOT interrupt mid-word ← Issue 2 working
- Hang up → `[INFO] Room call-... deleted — call fully torn down` ← clean teardown working

**Step 2 — Verify Issue 4 (tool toggles):**
Create profile with `enabled_tools = ["end_call"]`. Run call. Check logs: `Tools loaded: ['end_call']`. Try to trigger a booking — AI should say it can't.

**Step 3 — Address Issue 5 (latency) if Steps 1–2 pass.**

**After each session: update this file.**
