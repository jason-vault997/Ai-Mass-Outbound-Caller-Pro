# OutboundAI — Project Context for AI Assistants

> Paste this file into any new AI chat session to restore full context.
> Keep this file updated as the project evolves.

---

## What this project is

An AI-powered outbound voice calling platform.
- **AI voice agent** calls leads, greets them, books appointments, ends calls.
- **Stack:** LiveKit Agents 1.x + Gemini Live API + FastAPI + Supabase + APScheduler.
- **Telephony:** Vobiz SIP trunk → LiveKit SIP → agent room.
- **Deployment:** VPS via Coolify. All secrets in Coolify env vars (never in code).
- **Frontend:** Single-page dashboard at `ui/index.html` (vanilla JS, no build step).

---

## Repo structure

```
agent.py          — LiveKit agent entrypoint. Gemini Live session, greeting, watchdog, teardown.
server.py         — FastAPI backend. All REST API endpoints + dispatch logic.
db.py             — Async Supabase (Postgres) helpers for all tables.
tools.py          — 9 LLM function tools: end_call, book_appointment, check_availability, etc.
prompts.py        — Default system prompt. Instructs AI to be professional, speak first, etc.
ui/index.html     — Full dashboard: agent profiles, campaigns, CRM, call logs, settings.
supabase_schema.sql — Full DB schema. Safe to re-run (all IF NOT EXISTS).
.env.example      — All env vars documented.
```

---

## Key env vars (set in Coolify)

| Var | Purpose |
|-----|---------|
| `LIVEKIT_URL` | LiveKit Cloud WSS URL |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit credentials |
| `GEMINI_API_KEY` | Google AI Studio key |
| `SUPABASE_URL` / `SUPABASE_KEY` | Supabase project URL + anon key |
| `VOBIZ_SIP_TRUNK_ID` | SIP trunk ID from LiveKit dashboard |
| `MAX_CALL_DURATION_SECONDS` | Hard call cap, default 270 (4m30s) |
| `CALCOM_API_KEY` / `CALCOM_EVENT_TYPE_ID` | Cal.com booking (optional) |
| `TWILIO_*` | SMS confirmations (optional) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `S3_BUCKET` | Call recordings (optional) |

---

## Supabase tables

- `agent_profiles` — voice model configs. Columns: id, name, voice, model, system_prompt, enabled_tools, is_default, speaks_first, created_at
- `campaigns` — bulk outbound call campaigns. Columns include: agent_profile_id, contacts_json, schedule_type, schedule_time, call_delay_seconds
- `call_logs` — one row per call. Columns include: recording_url, notes, outcome
- `appointments` — booked appointments from tool calls
- `contact_memory` — per-contact AI memory/insights
- `settings` — key/value store for global config
- `error_logs` — structured error log from agent/server

---

## Fixes implemented (as of May 2026)

### Commit A — `7b68147` — AI greets first
- `agent.py`: On call connect, tries `session.generate_reply()` with greeting instructions, falls back to `session.say()`. Honors `agent_speaks_first` metadata flag (read from agent profile's `speaks_first` column).
- `server.py` + `db.py`: `speaks_first` field added to `AgentProfileRequest` and all CRUD ops.
- `ui/index.html`: "AI speaks first" checkbox in agent profile editor (default checked).
- `supabase_schema.sql`: `ALTER TABLE agent_profiles ADD COLUMN IF NOT EXISTS speaks_first INTEGER DEFAULT 1;`

### Commit B — `fa38d6b` — VAD start sensitivity
- `agent.py` `_build_session()`: Added `start_of_speech_sensitivity=START_SENSITIVITY_LOW` to Gemini Live `AutomaticActivityDetection` config. Prevents phone taps/breathing/background noise triggering false interrupts.

### Commit C — `ff27841` — Watchdog + duration cap + clean teardown
- **Watchdog:** Background asyncio task polls every 5s. Any session event bumps `activity_state["last"]`. If >20s idle, sends recovery prompt "Hello, are you still there?". Fixes Gemini Live VAD lockup (dead air after false interrupt).
- **Duration cap:** `MAX_CALL_DURATION_SECONDS` env var (default 270s). After cap, force-ends call.
- **Clean teardown:** After disconnect, calls `ctx.api.room.delete_room()` + `ctx.shutdown()`. Without this, LiveKit room/Egress/Gemini meter kept running after user hung up.

---

## Known pending items (not yet implemented)

- **Issue 4:** Verify tool toggles (end_call, booking, etc.) work correctly via logs.
- **Issue 5:** Reduce latency at call start (AI waiting for user speech). Deferred.
- No changes to `tools.py` or `prompts.py` planned yet.

---

## How to run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn server:app --host 0.0.0.0 --port 8000 &
python agent.py start
```

---

## How to deploy

Push to GitHub → Coolify auto-deploys from `main` branch.
Run `agent.py` as a separate worker process (separate Coolify service or same service with process manager).

---

## Rollback any commit

```bash
git revert <commit-hash> && git push
# or to hard reset to before all three fixes:
git reset --hard 27496ca && git push --force-with-lease origin main
```
