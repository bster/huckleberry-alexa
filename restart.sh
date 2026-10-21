#!/bin/bash
# Restart the live baby-tracker service, refusing to do so on a file that cannot
# even be compiled.
#
# This matters because of how the service is wired: launchd runs uvicorn straight
# out of this working directory with KeepAlive=true and ThrottleInterval=10. A
# file with a syntax error makes uvicorn fail at import, exit non-zero, and get
# relaunched ten seconds later into the same failure — a crash loop that leaves
# Alexa with no endpoint at all. Compiling first turns that outage into a refusal
# to restart, with the old process still serving.
#
# usage: ./restart.sh
set -euo pipefail

cd "$(dirname "$0")"
LABEL="gui/$(id -u)/com.local.babytracker"
PY=.venv/bin/python

echo "==> compiling"
for f in baby_tracker_server.py; do
    if ! "$PY" -m py_compile "$f"; then
        echo "REFUSING TO RESTART: $f does not compile. The running service is untouched." >&2
        exit 1
    fi
    echo "    $f OK"
done

# A model the console has not been given yet is only a warning, but a malformed
# one is worth catching before it is pasted in.
if [ -f alexa/interactionModel.json ]; then
    "$PY" -c 'import json,sys; json.load(open("alexa/interactionModel.json"))' \
        || { echo "REFUSING TO RESTART: alexa/interactionModel.json is not valid JSON." >&2; exit 1; }
    echo "    alexa/interactionModel.json OK"
fi

echo "==> restarting $LABEL"
launchctl kickstart -k "$LABEL"

echo "==> waiting for health"
for i in $(seq 1 30); do
    sleep 1
    body=$(curl -s -m 5 localhost:8765/status 2>/dev/null) || continue
    if printf '%s' "$body" | "$PY" -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)' 2>/dev/null; then
        echo "    healthy after ${i}s"
        printf '%s' "$body" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
print("    huckleberry:", d["huckleberry"])
print("    tailscale hostname matches:", d["tailscale_hostname"]["matches"])'
        exit 0
    fi
done

echo "SERVICE DID NOT COME BACK HEALTHY within 30s — check ~/Library/Logs/baby-tracker.error.log" >&2
exit 1
