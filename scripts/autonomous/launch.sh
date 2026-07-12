#!/bin/bash
# Simple launcher for autonomous daemon
# Usage: ./launch.sh [hours]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOURS="${1:-10}"

echo "=========================================="
echo "  AUTONOMOUS CLAUDE WORK DAEMON"
echo "=========================================="
echo "Hours per agent: $HOURS"
echo "Work directory: $WORK_DIR"
echo "Started: $(date)"
echo ""
echo "This will run in background. Check progress with:"
echo "  tail -f $WORK_DIR/deep_work_logs/daemon.log"
echo "  grep -c '^###' $WORK_DIR/deep_work_logs/*_findings.md"
echo ""
echo "To stop: kill \$(cat $WORK_DIR/deep_work_logs/daemon.pid)"
echo "=========================================="
echo ""

# Create log directory
mkdir -p "$WORK_DIR/deep_work_logs"

# Initialize findings files
for agent in performance security code_quality test_gaps; do
    findings_file="$WORK_DIR/deep_work_logs/${agent}_findings.md"
    if [ ! -f "$findings_file" ]; then
        cat > "$findings_file" << EOF
# ${agent^} Findings - Autonomous Deep Work Session

**Started:** $(date)
**Duration:** ${HOURS} hours

---

## Findings

EOF
    fi
done

# Launch daemon in background
cd "$WORK_DIR"
nohup python3 "$SCRIPT_DIR/daemon.py" \
    --hours "$HOURS" \
    --work-dir "$WORK_DIR" \
    --check-interval 60 \
    > "$WORK_DIR/deep_work_logs/daemon_stdout.log" 2>&1 &

DAEMON_PID=$!
echo $DAEMON_PID > "$WORK_DIR/deep_work_logs/daemon.pid"

echo "Daemon started with PID: $DAEMON_PID"
echo "Logs: $WORK_DIR/deep_work_logs/"
echo ""
echo "The daemon will:"
echo "  1. Launch all agents"
echo "  2. Monitor their progress every 60 seconds"
echo "  3. Resume agents that finish early"
echo "  4. Keep working until time expires"
echo ""
echo "You can safely disconnect. Work continues in background."
