#!/bin/bash
# Check status of autonomous daemon
# Usage: ./status.sh

WORK_DIR="/mnt/AI_Projects/Blackreach"
LOG_DIR="$WORK_DIR/deep_work_logs"

echo "=========================================="
echo "  AUTONOMOUS DAEMON STATUS"
echo "=========================================="
echo ""

# Check if daemon is running
if [ -f "$LOG_DIR/daemon.pid" ]; then
    PID=$(cat "$LOG_DIR/daemon.pid")
    if ps -p $PID > /dev/null 2>&1; then
        echo "Daemon: RUNNING (PID: $PID)"
    else
        echo "Daemon: STOPPED (stale PID file)"
    fi
else
    echo "Daemon: NOT RUNNING"
fi
echo ""

# Show findings count
echo "Findings count:"
for file in "$LOG_DIR"/*_findings.md; do
    if [ -f "$file" ]; then
        name=$(basename "$file" _findings.md)
        count=$(grep -c "^###" "$file" 2>/dev/null || echo "0")
        printf "  %-20s %d findings\n" "$name:" "$count"
    fi
done
echo ""

# Show last few log entries
echo "Recent daemon activity:"
if [ -f "$LOG_DIR/daemon.log" ]; then
    tail -10 "$LOG_DIR/daemon.log"
else
    echo "  No log file found"
fi
echo ""

# Show session summary if exists
if [ -f "$LOG_DIR/session_summary.json" ]; then
    echo "Session summary:"
    cat "$LOG_DIR/session_summary.json"
fi

echo "=========================================="
