#!/bin/bash
# Stop autonomous daemon gracefully
# Usage: ./stop.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$WORK_DIR/deep_work_logs"

if [ -f "$LOG_DIR/daemon.pid" ]; then
    PID=$(cat "$LOG_DIR/daemon.pid")
    if ps -p $PID > /dev/null 2>&1; then
        echo "Stopping daemon (PID: $PID)..."
        kill -SIGTERM $PID
        sleep 2
        if ps -p $PID > /dev/null 2>&1; then
            echo "Daemon still running, forcing..."
            kill -9 $PID
        fi
        rm "$LOG_DIR/daemon.pid"
        echo "Daemon stopped."
    else
        echo "Daemon not running (stale PID file)"
        rm "$LOG_DIR/daemon.pid"
    fi
else
    echo "No daemon PID file found"
fi

# Also kill any claude processes that might be orphaned
echo "Checking for orphaned claude processes..."
pkill -f "claude.*--print" 2>/dev/null && echo "Killed orphaned claude processes" || echo "No orphaned processes"
