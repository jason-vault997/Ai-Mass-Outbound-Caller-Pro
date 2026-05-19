# OutboundAI — Full Session Checkpoint

> **For any AI assistant starting a new session:**
> Read this entire file before touching any code.
> This is the single source of truth for everything that has been designed, built, debated, and deferred.
> Update this file at the end of every major session.

---

## 1. What this project is

An AI-powered **outbound voice calling SaaS platform**. The system automatically phones leads, has a real conversation (greets, qualifies, books appointments), and hangs up — all without a human agent.

**User (owner):** Jason. He runs this as a commercial platform for businesses that want automated outbound appointment-booking calls.

**Objective of the build sessions:** Make this production-grade. It worked on paper but had critical bugs that made real calls fail.

---

## 2. Full Technology Stack

| Layer | Technology |
|---|---|
| AI voice model | Google Gemini Live API (`gemini-3.1-flash-live-preview` default, also supports `gemini-2.5-flash-native-audio-preview-12-2025`) |
| Voice agent orchestration | LiveKit Agents 1.x (`livekit-agents` Python SDK) |
| Telephony / SIP | Vobiz SIP trunk → LiveKit SIP inbound/outbound |
| Backend API | FastAPI + Uvicorn, running on port 8000 |
| Database | Supabase (hosted Postgres), accessed async via `supabase-py` |
| Task scheduling | APScheduler (`AsyncIOScheduler`) for campaigns |
| Frontend dashboard | Single HTML file `ui/index.html` — vanilla JS, no build step, served by FastAPI |
| Deployment | VPS managed via Coolify. Auto-deploys from GitHub `main` branch |
| SSL fix | `certifi` is patched onto `ssl.create_default_context` at import time in both `agent.py` and `server.py` to fix certificate errors on VPS |
| Recordings | S3-compatible storage via LiveKit Egress (optional) |
| SMS | Twilio (optional, for booking confirmations) |
| Calendar | Cal.com API (optional, for calendar sync) |

**CRITICAL RULE:** Environment variables on the VPS/Coolify are the **single source of truth** for all credentials. Never write credentials into code. Never call `load_db_settings_to_env()` (this anti-pattern was explicitly removed). Per-call overrides (voice, model, prompt, tools) travel via LiveKit job metadata only.

---

## 3. Repository File Map

```
agent.py             — LiveKit worker. Dials SIP, runs Gemini Live session, greets lead,
                       runs watchdog, enforces call duration cap, tears down cleanly.
server.py            — FastAPI app. All REST endpoints. Call dispatch. Campaign runner.
                       Serves the dashboard HTML.
db.py                — All async Supabase DB helpers (CRUD for every table).
tools.py             — 9 LLM function tools the AI can call during a conversation.
prompts.py           — Default system prompt template + build_prompt() interpolator.
ui/index.html        — Complete single-page dashboard (agent profiles, campaigns,
                       CRM contacts, call logs, settings, live log stream).
supabase_schema.sql  — Full DB schema. Every statement is IF NOT EXISTS — safe to re-run.
.env.example         — Every env var documented with example values.
requirements.txt     — Python dependencies.
CONTEXT.md           — THIS FILE. AI session continuity checkpoint.
```

---

## 4. Environment Variables (all set in Coolify)

### Required — will not work without these
| Variable | What it is |
|---|---|
| `LIVEKIT_URL` | LiveKit Cloud WebSocket URL (wss://...) |
| `LIVEKIT_API_KEY` | LiveKit project API key |
| `LIVEKIT_API_SECRET` | LiveKit project API secret |
| `GOOGLE_API_KEY` | Google AI Studio key for Gemini Live |
| `SUPABASE_URL` | Supabase project URL (https://xxx.supabase.co) |
| `SUPABASE_KEY` | Supabase anon/service key |
| `OUTBOUND_TRUNK_ID` | SIP trunk ID from LiveKit dashboard (Vobiz trunk) |

### Optional — features degrade gracefully if missing
| Variable | What it enables |
|---|---|
| `GEMINI_MODEL` | Override default model (default: `gemini-3.1-flash-live-preview`) |
| `GEMINI_TTS_VOICE` | Override default voice (default: `Aoede`) |
| `USE_GEMINI_REALTIME` | Set `false` to force pipeline fallback (default: `true`) |
| `MAX_CALL_DURATION_SECONDS` | Hard call cap in seconds (default: `270` = 4m30s) |
| `DEFAULT_TRANSFER_NUMBER` | SIP/phone number to transfer calls to a human |
| `VOBIZ_SIP_DOMAIN` | SIP domain for transfer formatting |
| `DEEPGRAM_API_KEY` | Enables pipeline fallback STT (Deepgram Nova-2) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_FROM_NUMBER` | SMS confirmation texts |
| `CALCOM_API_KEY` / `CALCOM_EVENT_TYPE_ID` / `CALCOM_TIMEZONE` | Cal.com calendar booking |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_BUCKET` / `S3_ENDPOINT_URL` / `S3_REGION` | Call recording to S3 |
| `HOST` / `PORT` | Override server bind address (default 0.0.0.0:8000) |

---

## 5. Database Schema (Supabase / Postgres)

All tables use `DISABLE ROW LEVEL SECURITY` for simplicity. All IDs are UUID strings.

### `agent_profiles`
Stores different AI persona configurations.
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
voice TEXT NOT NULL DEFAULT 'Aoede',
model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-live-preview',
system_prompt TEXT,           -- null = use global prompt from settings
enabled_tools TEXT DEFAULT '[]',  -- JSON array of tool names. empty = all tools on
is_default INTEGER DEFAULT 0,
speaks_first INTEGER DEFAULT 1,   -- 1 = AI greets immediately, 0 = wait for user
created_at TEXT NOT NULL
```
> **Migration needed for existing installs:**
> `ALTER TABLE agent_profiles ADD COLUMN IF NOT EXISTS speaks_first INTEGER DEFAULT 1;`

### `campaigns`
Bulk outbound call campaigns with scheduling.
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'active',   -- active | paused | completed
contacts_json TEXT NOT NULL DEFAULT '[]',  -- JSON array of contact objects
schedule_type TEXT NOT NULL DEFAULT 'once',  -- once | daily
schedule_time TEXT DEFAULT '09:00',
call_delay_seconds INTEGER DEFAULT 3,
system_prompt TEXT,
created_at TEXT NOT NULL,
last_run_at TEXT,
total_dispatched INTEGER DEFAULT 0,
total_failed INTEGER DEFAULT 0,
agent_profile_id TEXT   -- FK → agent_profiles.id (nullable)
```

### `call_logs`
One row per call, written by `end_call` tool.
```sql
id TEXT PRIMARY KEY,
phone_number TEXT NOT NULL,
lead_name TEXT,
outcome TEXT,        -- booked | not_interested | wrong_number | voicemail | no_answer | callback_requested
reason TEXT,
duration_seconds INTEGER,
timestamp TEXT NOT NULL,
recording_url TEXT,  -- S3 URL if recording was enabled
notes TEXT           -- editable from dashboard
```

### `appointments`
Booked appointments, written by `book_appointment` tool.
```sql
id TEXT PRIMARY KEY,
name TEXT NOT NULL,
phone TEXT NOT NULL,
date TEXT NOT NULL,
time TEXT NOT NULL,
service TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'booked',
created_at TEXT NOT NULL,
calcom_booking_uid TEXT   -- Cal.com UID if synced
```

### `contact_memory`
Per-phone-number AI memory that persists across calls.
```sql
id TEXT PRIMARY KEY,
phone_number TEXT NOT NULL,
insight TEXT NOT NULL,
created_at TEXT NOT NULL
```
Index: `idx_contact_memory_phone ON contact_memory (phone_number)`

### `settings`
Key-value store for global config editable from the dashboard.
```sql
key TEXT PRIMARY KEY,
value TEXT NOT NULL,
updated_at TEXT NOT NULL
```
Important keys: `system_prompt`, and any credential overrides.

### `error_logs`
Structured log from both agent and server. Shown as live log stream in dashboard.
```sql
id TEXT PRIMARY KEY,
source TEXT NOT NULL,   -- 'agent' | 'server'
level TEXT NOT NULL DEFAULT 'error',  -- info | warning | error
message TEXT NOT NULL,
detail TEXT,
timestamp TEXT NOT NULL
```

---

## 6. How a Call Works (end-to-end flow)

1. User clicks "Call" in dashboard → POST `/api/call` with phone, lead name, business name, agent profile ID.
2. `server.py` resolves agent profile → extracts voice, model, tools, system prompt, `speaks_first`.
3. Server calls LiveKit API to **create a room** (`call-+91xxx-1234`) and **dispatch an agent job** with all context as JSON metadata.
4. `agent.py` `entrypoint()` receives the job. Parses metadata. Builds system prompt via `build_prompt()`.
5. Agent calls `ctx.api.sip.create_sip_participant()` with `wait_until_answered=True` — this **dials the phone** through Vobiz trunk and **blocks until the human picks up**.
6. Once answered, `_build_session()` creates a Gemini Live `AgentSession` with VAD config.
7. `session.start()` connects the AI to the room audio.
8. **Greeting block** runs — tries `session.generate_reply()`, falls back to `session.say()` if blocked.
9. Conversation runs. AI calls tools (`check_availability`, `book_appointment`, `end_call`, etc.) as needed.
10. **Watchdog** monitors activity. Dead air >20s → inject recovery prompt.
11. Call ends via: (a) AI calls `end_call` tool → `ctx.room.disconnect()`, (b) human hangs up → `participant_disconnected` event fires, or (c) `MAX_CALL_DURATION_SECONDS` timer expires.
12. **Teardown:** `session.aclose()` → `ctx.api.room.delete_room()` → `ctx.shutdown()`. Room deletion stops Egress recording and prevents lingering Gemini Live meter charges.

---

## 7. The 9 AI Tools (tools.py)

All live in `class AppointmentTools(llm.ToolContext)`. Enabled/disabled per agent profile via `enabled_tools` JSON array. Empty array = all tools enabled.

| Tool | What it does |
|---|---|
| `check_availability(date, time)` | Queries `appointments` table for slot conflicts |
| `book_appointment(name, phone, date, time, service)` | Inserts to `appointments`, optionally syncs Cal.com |
| `end_call(outcome, reason)` | Logs call to `call_logs`, calls `ctx.room.disconnect()` |
| `transfer_to_human(reason)` | SIP REFER to `DEFAULT_TRANSFER_NUMBER` |
| `send_sms_confirmation(phone, message)` | Sends SMS via Twilio |
| `lookup_contact(phone)` | Retrieves prior call history + memory for a phone number |
| `remember_details(detail)` | Saves an insight to `contact_memory` |
| `book_calcom(name, email, date, time)` | Books directly via Cal.com API |
| `cancel_calcom(booking_uid)` | Cancels a Cal.com booking |

---

## 8. The AI Persona & System Prompt (prompts.py)

Default persona: **"Priya"** — warm, professional appointment-booking assistant.

`DEFAULT_SYSTEM_PROMPT` in `prompts.py` instructs:
- **SPEAK FIRST** — open with "Hi, am I speaking with {lead_name}?" the moment the call connects.
- Full 6-step call flow: confirm identity → introduce → qualify → find slot → book → close.
- Objection handling scripts (busy, not interested, "are you a bot?", "stop calling", etc.).
- Style rules: max 1–2 short sentences per turn, no filler openers, no "As an AI".
- Tool usage rules: `lookup_contact` first, always `check_availability` before booking, always `end_call` at end.

`build_prompt(lead_name, business_name, service_type, custom_prompt)` interpolates `{lead_name}`, `{business_name}`, `{service_type}` into the template. If `custom_prompt` is provided (from agent profile or per-call override), it replaces the default template entirely.

---

## 9. The 5 Production Issues — Status of Each

### Issue 1 — AI never started the conversation ✅ FIXED (Commit A)
**Root cause:** The original code had a `if "3.1" in active_model or "2.5" in active_model: skip generate_reply()` block, assuming Gemini native-audio would speak autonomously from the system prompt. It didn't. The AI sat silent waiting for the human to speak first, which never happened on an outbound call because humans pick up and wait for the caller to speak.

**Fix:** Removed the model-name check entirely. Replaced with a try/except greeting flow:
```python
# In agent.py entrypoint(), after session.start():
if not agent_speaks_first:
    # wait for user (inbound-style)
else:
    try:
        await session.generate_reply(instructions="The call has just connected. Speak immediately. Open with: 'Hi, am I speaking with {lead_name}?'")
    except Exception:
        await session.say("Hi, am I speaking with {lead_name}?", allow_interruptions=True)
```

**New feature added:** `speaks_first` toggle on agent profiles. DB column `speaks_first INTEGER DEFAULT 1`. Checkbox in dashboard. Metadata key `agent_speaks_first` passed to agent. Default True (AI always greets first). Can be set False for inbound-style flows.

**Files changed:** `agent.py` (entrypoint greeting block), `server.py` (AgentProfileRequest + dispatch metadata), `db.py` (create/update agent profile), `ui/index.html` (checkbox + save/edit/reset), `supabase_schema.sql` (ALTER TABLE migration).

---

### Issue 2 — Microphone sensitivity too high causing constant interruptions ✅ FIXED (Commit B)
**Root cause:** Gemini Live's `AutomaticActivityDetection` defaults to `START_SENSITIVITY_HIGH`. Any phone tap, background noise, breathing, or shuffle was classified as the human starting to speak, which interrupted the AI mid-sentence. The AI was stopping every few words.

**Fix:** One line added to `_build_session()` in `agent.py`:
```python
realtime_input = _gt.RealtimeInputConfig(
    automatic_activity_detection=_gt.AutomaticActivityDetection(
        start_of_speech_sensitivity=_gt.StartSensitivity.START_SENSITIVITY_LOW,  # ← THIS LINE
        end_of_speech_sensitivity=_gt.EndSensitivity.END_SENSITIVITY_LOW,
        silence_duration_ms=2000,
        prefix_padding_ms=200,
    ),
)
```
`END_SENSITIVITY_LOW` was already there. `START_SENSITIVITY_LOW` was the missing piece. LOW start-sensitivity means the VAD requires a clearer, longer speech onset before classifying it as "user speaking". Real human speech still triggers it; taps and noise don't.

**Files changed:** `agent.py` only (1 line added inside `_build_session()`).

---

### Issue 3 — AI never recovers after being interrupted (dead air) ✅ FIXED (Commit C)
**Root cause:** When a false interrupt occurred (from Issue 2), the Gemini Live VAD entered a "waiting for user to finish speaking" state. Since the noise event was brief, the "end of user speech" threshold was never confidently met, and the AI sat waiting forever — dead air — while the call meter kept running.

**Fix:** Three-layer defence system added to `agent.py` `entrypoint()` after the greeting block:

**Layer 1 — Watchdog task:**
```python
activity_state = {"last": time.time()}

# Hooks onto all session events to bump the timer
for evt in ("user_speech_committed", "agent_speech_committed", "user_state_changed", ...):
    session.on(evt, _bump_activity)

async def _watchdog():
    while not disconnect_event.is_set():
        await asyncio.sleep(5)
        if time.time() - activity_state["last"] > 20:  # 20s of dead air
            try:
                await session.generate_reply(instructions="Say: 'Hello, are you still there?'")
            except:
                await session.say("Hello, are you still there?", allow_interruptions=True)
            _bump_activity()  # reset timer after recovery

watchdog_task = asyncio.create_task(_watchdog())
```

**Layer 2 — Hard duration cap:**
```python
max_call_duration = int(os.getenv("MAX_CALL_DURATION_SECONDS", "270"))  # 4m30s
await asyncio.wait_for(disconnect_event.wait(), timeout=max_call_duration)
# On timeout: force-sets disconnect_event, watchdog gets cancelled, teardown begins
```
Cap is 270 seconds (4 minutes 30 seconds) as explicitly requested by the user. Was previously 3600s (1 hour) — an enormous cost risk.

**Layer 3 — Clean room teardown:**
```python
await session.aclose()
await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))
await _log("info", f"Room {ctx.room.name} deleted — call fully torn down")
ctx.shutdown()
```
Without `delete_room()`, the LiveKit room survived the SIP disconnect. Egress recordings kept running. Gemini Live session meter kept charging. `ctx.shutdown()` was also missing, leaving zombie worker processes.

**Files changed:** `agent.py` (entire tail of entrypoint replaced), `.env.example` (added `MAX_CALL_DURATION_SECONDS` entry).

---

### Issue 4 — Tool toggles (end_call, booking, etc.) — ⏳ PENDING VERIFICATION
**Status:** Not modified. The toggle mechanism exists and appears correct in code:
- `enabled_tools` column on `agent_profiles` is a JSON string like `["end_call","book_appointment"]`.
- Empty array or null = all 9 tools enabled.
- `AppointmentTools.build_tool_list(enabled)` filters the full method list by name.
- Dashboard has a text input for the JSON array.

**Still to do:** Run a real call with a specific tools-restricted profile, check logs for `Tools loaded: [...]` line, verify the AI can't call disabled tools.

---

### Issue 5 — Latency at call start — ⏳ DEFERRED
**Status:** Not addressed yet. Deferred to a later session after Issues 1–3 are verified working in production.

**The problem:** There's a delay between the human picking up and the AI greeting. Part of this is Gemini Live session startup time, part is SIP audio negotiation.

**Ideas noted for future:** Pre-warm the session before `wait_until_answered`, explore session resumption to avoid cold starts.

---

## 10. Agent Profile — Full Data Flow

When user saves an agent profile in the dashboard:
1. `ui/index.html` `saveAgentProfile()` → POST/PUT `/api/agent-profiles` with body: `{name, voice, model, system_prompt, enabled_tools, is_default, speaks_first}`.
2. `server.py` `AgentProfileRequest` receives it. `speaks_first` is a `bool`.
3. `db.py` `create_agent_profile()` or `update_agent_profile()` stores it. `speaks_first` stored as `INTEGER` (1 or 0).

When a call is dispatched with that profile:
4. `server.py` `api_dispatch_call()` fetches profile, reads `speaks_first`.
5. `effective_speaks_first = profile.get("speaks_first") != 0` — NULL/missing → True (backwards-compat).
6. `metadata["agent_speaks_first"] = bool(effective_speaks_first)` goes into job metadata JSON.
7. `agent.py` `_read(meta)` parses it: `if "agent_speaks_first" in meta: agent_speaks_first = bool(meta["agent_speaks_first"])`.
8. Greeting block honours it.

Same flow for campaigns — `_dispatch_one()` does the same profile resolution.

---

## 11. The `_build_session()` Function — Critical Details

Located in `agent.py`. Builds the `AgentSession` with Gemini Live preferred, Deepgram pipeline as fallback.

**Silence prevention configs (ALL THREE ARE MANDATORY):**
1. `SessionResumptionConfig(transparent=True)` — auto-reconnects the Gemini Live WebSocket on timeout without losing conversation context.
2. `ContextWindowCompressionConfig(trigger_tokens=25600, sliding_window=SlidingWindow(target_tokens=12800))` — prevents Gemini from freezing when the context window fills up on long calls.
3. `RealtimeInputConfig` with `START_SENSITIVITY_LOW` + `END_SENSITIVITY_LOW` + `silence_duration_ms=2000` + `prefix_padding_ms=200` — the VAD settings.

**Plugin compatibility:** The plugin API has two possible parameter names (`session_resumption` vs `session_resumption_config`). The code tries both spelling variations in nested try/except to handle different plugin versions.

**Pipeline fallback:** If Gemini Live is unavailable or `USE_GEMINI_REALTIME=false`, falls back to `Deepgram STT + Gemini LLM + Gemini TTS` pipeline with Silero VAD. Requires `DEEPGRAM_API_KEY`.

---

## 12. Git History (recent, most recent first)

| Commit | Description |
|---|---|
| `469b7ab` | docs: add CONTEXT.md for AI assistant session continuity |
| `ff27841` | feat(issue-3): activity watchdog + 4m30s duration cap + clean room teardown |
| `fa38d6b` | fix(issue-2): explicit START_SENSITIVITY_LOW so AI is harder to false-interrupt |
| `7b68147` | feat(issue-1): AI greets first by default + agent_speaks_first toggle |
| `27496ca` | feat: add /api/setup/trunks list endpoint and dashboard diagnostic |
| `69bd9d1` | feat: make HOST/PORT env-overridable for proxy compatibility |
| `9311b80` | feat: rebuild as OutboundAI SaaS with Gemini Live + Supabase dashboard |

**Last known stable baseline (before Issues 1–3 fixes):** `27496ca`

---

## 13. Rollback Instructions

The user explicitly asked for all fixes to be individually revertable. Each issue is a separate commit.

```bash
# Revert just Issue 3 fix (watchdog/cap/teardown)
git revert ff27841 && git push

# Revert just Issue 2 fix (VAD sensitivity)
git revert fa38d6b && git push

# Revert just Issue 1 fix (greeting / speaks_first)
git revert 7b68147 && git push

# Revert all three fixes at once
git revert --no-commit ff27841 fa38d6b 7b68147 && git commit -m "revert issues 1-3 fixes" && git push

# Nuclear: hard reset to before all fixes (loses history)
git reset --hard 27496ca && git push --force-with-lease origin main
```

---

## 14. How to Run

### Locally
```bash
pip install -r requirements.txt
cp .env.example .env        # fill in real values
uvicorn server:app --host 0.0.0.0 --port 8000 &
python agent.py start
```
Dashboard at http://localhost:8000

### Production (Coolify)
- Push to GitHub `main` → Coolify auto-builds and deploys.
- `server.py` runs as the web service (Uvicorn, port 8000).
- `agent.py` runs as a **separate worker process** — it must be running alongside the server for calls to work.
- Both processes read env vars from Coolify's environment variable panel.

### Supabase setup
Run `supabase_schema.sql` once in Supabase → SQL Editor. Safe to re-run anytime.

For existing installs that pre-date the `speaks_first` column, run this one extra migration:
```sql
ALTER TABLE agent_profiles ADD COLUMN IF NOT EXISTS speaks_first INTEGER DEFAULT 1;
```

---

## 15. User Preferences & Decisions Made

- **No experimental changes.** User explicitly said: prioritize stability and reliability. Every fix must be surgical and reversible.
- **No touching `tools.py` or `prompts.py`** until Issues 1–3 are verified in production.
- **4 minutes 30 seconds** (270s) hard call cap. User specifically asked for this — not 8 minutes, not 5 minutes.
- **`speaks_first` defaults to True.** All existing profiles treat NULL as "AI speaks first" for backwards-compat.
- **All credentials in Coolify env vars.** Never in DB, never in code. The `load_db_settings_to_env()` anti-pattern was removed.
- **Separate commits per issue.** Makes rollback clean and auditable.
- **GitHub repo:** `https://github.com/jason-vault997/Ai-Mass-Outbound-Caller-Pro.git` (main branch)

---

## 16. What to Do Next Session

1. **Verify Issues 1–3 are working in production** — do a real test call, check logs for:
   - `[INFO] Greeting triggered via generate_reply` or `Greeting triggered via session.say()`
   - `[WARNING] Dead-air detected (Xs) — sending recovery prompt` (if you stay silent for 20s)
   - `[WARNING] Hard duration cap reached (270s)` (if call goes long)
   - `[INFO] Room call-... deleted — call fully torn down` (after hang-up)

2. **Verify Issue 4 — tool toggles** — create an agent profile with `enabled_tools = ["end_call"]`, run a call, check log line `Tools loaded: ['end_call']`, verify AI can't book.

3. **If all good, address Issue 5** — call start latency.

4. **Update this file** after each session.
