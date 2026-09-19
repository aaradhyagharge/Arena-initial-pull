#!/usr/bin/env bash
# start-harness.sh — one command: starts the Arena Harness + a Cloudflare tunnel,
# then prints a copy-paste block for the chat. Ctrl+C stops everything.
#
# Usage:  ./start-harness.sh [ROOT_FOLDER]     (default: current folder)

ROOT="${1:-$PWD}"
PORT=8787
TOKEN=$(openssl rand -hex 16)
HERE="$(cd "$(dirname "$0")" && pwd)"

command -v cloudflared >/dev/null 2>&1 || { echo "cloudflared not found. Install it first: brew install cloudflared"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 not found. Install Python first."; exit 1; }

echo "starting harness (root: $ROOT, port: $PORT)..."
python3 "$HERE/arena_harness.py" --root "$ROOT" --port "$PORT" --token "$TOKEN" &
HPID=$!

LOG=$(mktemp /tmp/arena-tunnel.XXXXXX)
cloudflared tunnel --url "http://127.0.0.1:$PORT" > "$LOG" 2>&1 &
CPID=$!

cleanup() {
  echo
  echo "stopping harness and tunnel..."
  kill "$CPID" "$HPID" 2>/dev/null
  rm -f "$LOG"
  exit 0
}
trap cleanup INT TERM

URL=""
for i in $(seq 1 30); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1)
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "$URL" ]; then
  echo "tunnel did not come up in 30s — last log lines:"
  tail -5 "$LOG"
  cleanup
fi

echo "======================================================"
echo " ARENA HARNESS is LIVE — copy this block to the chat:"
echo
echo "   URL   : $URL"
echo "   TOKEN : $TOKEN"
echo "   FOLDER: $ROOT"
echo
echo " Stop anytime: Ctrl+C in THIS window (kills everything)."
echo "======================================================"
wait "$CPID" 2>/dev/null
cleanup
