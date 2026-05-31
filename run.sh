#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$DIR/venv/bin/python"

# Kill any existing daemon on port 19876
fuser -k 19876/tcp 2>/dev/null

# Start daemon in background
"$VENV_PYTHON" "$DIR/daemon/daemon.py" &
DAEMON_PID=$!

# Wait for daemon to start and launch GUI
sleep 3
"$VENV_PYTHON" "$DIR/gui/manager.py" &

wait $DAEMON_PID
