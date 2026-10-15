#!/usr/bin/env bash
# Stops the Sweet Search web application started by start.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT/.run/app.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Sweet Search is not running (no PID file)."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if ! kill -0 "$PID" 2>/dev/null; then
  echo "Sweet Search is not running (stale PID file removed)."
  rm -f "$PID_FILE"
  exit 0
fi

# Only stop the process if it is really this application, in case the PID was reused.
if ! tr '\0' ' ' <"/proc/$PID/cmdline" 2>/dev/null | grep -q "src/app.py"; then
  echo "PID $PID is not the Sweet Search process; leaving it alone and removing the PID file."
  rm -f "$PID_FILE"
  exit 0
fi

kill "$PID"
for _ in $(seq 1 20); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "Sweet Search stopped."
    exit 0
  fi
  sleep 0.5
done

kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "Sweet Search did not exit gracefully and was force-stopped."
