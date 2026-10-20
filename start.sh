#!/usr/bin/env bash
# Starts the Pie Hard web application in the background.
# Optional environment variables: HOST (default 127.0.0.1) and PORT (default 5000).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
PID_FILE="$RUN_DIR/app.pid"
LOG_FILE="$RUN_DIR/app.log"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"

mkdir -p "$RUN_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Pie Hard is already running (PID $(cat "$PID_FILE")) at http://$HOST:$PORT"
  exit 0
fi
rm -f "$PID_FILE"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

cd "$ROOT"
HOST="$HOST" PORT="$PORT" nohup "$PYTHON" src/app.py serve >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

for _ in $(seq 1 30); do
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Pie Hard failed to start. Last log lines:" >&2
    tail -n 20 "$LOG_FILE" >&2
    rm -f "$PID_FILE"
    exit 1
  fi
  if curl -fsS -o /dev/null "http://$HOST:$PORT/api/status" 2>/dev/null; then
    echo "Pie Hard started (PID $(cat "$PID_FILE"))."
    echo "  Search:    http://$HOST:$PORT/"
    echo "  Back-office: http://$HOST:$PORT/backoffice"
    echo "  Log:       $LOG_FILE"
    exit 0
  fi
  sleep 0.5
done

echo "Pie Hard did not respond within 15 seconds. See $LOG_FILE" >&2
exit 1
