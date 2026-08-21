#!/bin/bash
# Development instance. Bound to 127.0.0.1 so it is unreachable from the funnel,
# and on 8766 so it can never collide with the live service on 8765.
set -euo pipefail
cd "$(dirname "$0")"
python -m py_compile baby_tracker_server.py
exec .venv/bin/uvicorn baby_tracker_server:app --host 127.0.0.1 --port 8766 "$@"
