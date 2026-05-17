# OutboundAI — AI Voice Calling SaaS

A **production-grade AI outbound voice calling platform** built around **Gemini Live**, **LiveKit Agents**, **Vobiz SIP**, and **Supabase**, with a single-page dashboard for campaigns, CRM, agent profiles, and BYOK configuration.

> Evolved from the original `LIvekitAIVoice` (OpenAI/Deepgram/Groq pipeline). The legacy CLI utilities (`make_call.py`, `create_trunk.py`, `list_trunks.py`, `setup_trunk.py`, `config.py`) are kept around for reference and one-off ops, but the new architecture runs entirely through the FastAPI dashboard at port 8000.

---

## 🚀 Architecture

| Layer | Tech |
|---|---|
| Voice AI | Google **Gemini Live** (`gemini-3.1-flash-live-preview`) — single realtime model, no separate STT/TTS |
| Voice orchestration | LiveKit Agents 1.x + LiveKit Cloud |
| Telephony | Vobiz SIP outbound trunk |
| API server | FastAPI + Uvicorn (port 8000) |
| Database | Supabase (Postgres) |
| Scheduling | APScheduler (cron-style campaigns) |
| Recording | LiveKit Egress → S3-compatible storage |
| UI | Single `ui/index.html` (vanilla JS + Chart.js CDN) |
| Container | Docker → Coolify |

---

## 📁 Project structure

```
.
├── agent.py              ← LiveKit worker (Gemini Live entrypoint, dial-first)
├── server.py             ← FastAPI REST API + APScheduler
├── db.py                 ← Async Supabase data layer
├── tools.py              ← 9 LLM function tools
├── prompts.py            ← Prompt template + builder
├── start.sh              ← uvicorn :8000 + agent worker
├── Dockerfile            ← CMD: sh start.sh
├── requirements.txt
├── supabase_schema.sql   ← Run once in Supabase SQL Editor
├── .env.example
├── ui/index.html         ← Full single-page dashboard
└── (legacy: make_call.py, setup_trunk.py, create_trunk.py, list_trunks.py, config.py)
```

---

## 🛠 First-time setup

1. **Supabase** — create a project → SQL Editor → paste `supabase_schema.sql` → Run.
2. **LiveKit Cloud** — create a project → grab `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
3. **Gemini API key** — `aistudio.google.com/app/apikey`.
4. **Vobiz SIP** — note your domain, username, password, outbound number.
5. Copy `.env.example` to `.env` and fill in everything (OR set them via the dashboard Settings tab).

### Local run

```powershell
pip install -r requirements.txt
sh start.sh
```

Then open `http://localhost:8000` and:

1. Settings → fill in LiveKit, Gemini, Vobiz → click **⚡ Create SIP Trunk**.
2. ✏️ AI Prompt → customise for your business.
3. 🤖 Agents → create at least one profile.
4. 📞 Single Call → test with your own number.

### Docker / Coolify

```bash
docker build -t outboundai .
docker run -p 8000:8000 --env-file .env outboundai
```

In Coolify: New Resource → GitHub → Dockerfile auto-detected → set env vars → set port `8000` → Deploy.

---

## 🔑 Critical rules

| # | Rule |
|---|---|
| 1 | **Dial first** — `await ctx.api.sip.create_sip_participant(..., wait_until_answered=True)` BEFORE `session.start()` |
| 2 | **Never** use `close_on_disconnect=True` with SIP. Watch `participant_disconnected` event instead. |
| 3 | Gemini 3.1 / 2.5 native-audio models speak autonomously — do **NOT** call `generate_reply()` on them. |
| 4 | Use `EndSensitivity.END_SENSITIVITY_LOW` (full string), not `.LOW`. |
| 5 | All 3 silence-prevention configs are mandatory (resumption + compression + RealtimeInputConfig). |
| 6 | FastAPI on port 8000. The agent worker uses port 8081 internally. |
| 7 | Settings priority: env vars → Supabase `settings` table → empty defaults. |

---

## 💰 Cost (per minute)

| Service | ₹ / min |
|---|---|
| Vobiz SIP | 1.00 |
| LiveKit Cloud | 0.17 |
| Gemini Live | 0.03 |
| **Total** | **≈ 1.20** |

A 2-minute call ≈ ₹2.40.

---

## 🛟 Troubleshooting

| Symptom | Fix |
|---|---|
| Call drops at exactly 60s | Check that `close_on_disconnect=True` is NOT set. |
| Agent goes silent after 30–90s | Confirm `EndSensitivity.END_SENSITIVITY_LOW` is the full enum string. |
| 1008 error on session start | Switch from `gemini-2.0-flash-live-001` to `gemini-3.1-flash-live-preview`. |
| `profiles.map is not a function` in UI | Run `supabase_schema.sql` to create `agent_profiles` table. |
| Worker uses old model after Settings change | Redeploy — `load_db_settings_to_env` runs only at worker startup. |
| `SSL certificate verify failed` | Already patched at top of `agent.py` and `server.py` via certifi. |

---

## 🧰 Legacy CLI utilities (still work)

- `python make_call.py --to +91XXXXXXXXXX` — single test dial via the agent.
- `python list_trunks.py` — list configured SIP trunks.
- `python create_trunk.py` — create a SIP trunk via CLI.

These pre-date the dashboard and don't use Supabase. Prefer the dashboard for production.
