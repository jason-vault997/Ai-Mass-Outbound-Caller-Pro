#!/bin/sh
# Container entrypoint. Single source of truth for env vars = the VPS / Coolify env.
# A local .env file is loaded ONLY for dev convenience (won't exist in prod).

set -e
cd "$(dirname "$0")"

echo "🚀 Starting Outbound Mass Caller..."

if [ -f ".env" ]; then
    echo "ℹ️  Loading dev .env (won't exist on the VPS — that's fine)"
    set -a
    . ./.env
    set +a
fi

# Bind config — env-overridable so Coolify / Heroku / Railway can set PORT directly.
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

echo "📋 Effective config (from environment):"
echo "   HOST         = ${HOST}"
echo "   PORT         = ${PORT}"
echo "   LIVEKIT_URL  = ${LIVEKIT_URL:-<unset>}"
echo "   GEMINI_MODEL = ${GEMINI_MODEL:-gemini-3.1-flash-live-preview}"
echo "   SUPABASE_URL = ${SUPABASE_URL:-<unset>}"
echo "   TRUNK_ID     = ${OUTBOUND_TRUNK_ID:-<unset>}"

SERVER_PID=""
AGENT_PID=""

cleanup() {
    echo "↓ Stopping..."
    [ -n "$SERVER_PID" ] && kill -TERM "$SERVER_PID" 2>/dev/null || true
    [ -n "$AGENT_PID" ]  && kill -TERM "$AGENT_PID"  2>/dev/null || true
    wait 2>/dev/null || true
}
# Forward shutdown signals so the container exits cleanly.
trap cleanup TERM INT

echo "🌐 Starting FastAPI server on ${HOST}:${PORT}..."
uvicorn server:app --host "${HOST}" --port "${PORT}" &
SERVER_PID=$!

sleep 2

echo "🤖 Starting LiveKit agent worker (foreground)..."
python -u agent.py start &
AGENT_PID=$!

# POSIX-portable supervision: poll until either process exits, then tear down.
while kill -0 "$SERVER_PID" 2>/dev/null && kill -0 "$AGENT_PID" 2>/dev/null; do
    sleep 5
done

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "✗ FastAPI server exited."
elif ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo "✗ Agent worker exited."
fi

cleanup
exit 1
