#!/bin/sh
# Container entrypoint for the no-build setup.
# Kept as a script (rather than an inline `sh -c "..."`) because the legacy
# Synology Docker app's Command field splits on spaces and ignores quotes,
# which breaks multi-step inline commands. A script file needs no quoting:
# just set the container command to:  sh /app/start.sh
set -e
cd /app
pip install --no-cache-dir -r requirements.txt
exec uvicorn main:app --host 0.0.0.0 --port 8000
