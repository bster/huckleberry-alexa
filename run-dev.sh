#!/bin/bash
# Development instance. Bound to 127.0.0.1 so it is unreachable from the funnel.
#
# Port 8767, not 8766: com.local.babytracker.setup already holds 8766 for the
# credential setup server. Two listeners on one port do not reliably fail on
# macOS — one binds 0.0.0.0 and the other 127.0.0.1 and connections go to
# whichever the kernel prefers — so a collision here would look like it worked
# and then behave strangely.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8767}"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $PORT is already in use — refusing to start a second listener on it:" >&2
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
    exit 1
fi

.venv/bin/python -m py_compile baby_tracker_server.py
exec .venv/bin/uvicorn baby_tracker_server:app --host 127.0.0.1 --port "$PORT" "$@"
