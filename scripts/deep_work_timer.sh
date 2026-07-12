#!/bin/bash
# Deep Work Timer - Forces agents to work for specified duration
# Tracks elapsed time and logs activity

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$WORK_DIR/deep_work_logs"
AGENT_NAME="${1:-unnamed}"
DURATION_HOURS="${2:-2}"
DURATION_SECONDS=$((DURATION_HOURS * 3600))

mkdir -p "$LOG_DIR"

START_TIME=$(date +%s)
END_TIME=$((START_TIME + DURATION_SECONDS))
LOG_FILE="$LOG_DIR/${AGENT_NAME}_$(date +%Y%m%d_%H%M%S).log"
FINDINGS_FILE="$LOG_DIR/${AGENT_NAME}_findings.md"

echo "=== Deep Work Session ===" | tee "$LOG_FILE"
echo "Agent: $AGENT_NAME" | tee -a "$LOG_FILE"
echo "Duration: ${DURATION_HOURS} hours" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "End Time: $(date -d @$END_TIME)" | tee -a "$LOG_FILE"
echo "Findings: $FINDINGS_FILE" | tee -a "$LOG_FILE"
echo "=========================" | tee -a "$LOG_FILE"

# Initialize findings file
cat > "$FINDINGS_FILE" << EOF
# Deep Work Findings: $AGENT_NAME
Started: $(date)
Duration: ${DURATION_HOURS} hours

## Session Log

EOF

# Function to get remaining time
get_remaining() {
    local now=$(date +%s)
    local remaining=$((END_TIME - now))
    if [ $remaining -lt 0 ]; then
        echo "0"
    else
        echo "$remaining"
    fi
}

# Function to format time
format_time() {
    local seconds=$1
    local hours=$((seconds / 3600))
    local minutes=$(((seconds % 3600) / 60))
    local secs=$((seconds % 60))
    printf "%02d:%02d:%02d" $hours $minutes $secs
}

# Export functions and variables for child processes
export START_TIME END_TIME LOG_FILE FINDINGS_FILE AGENT_NAME
export -f get_remaining format_time

# Create a status file that can be checked
STATUS_FILE="$LOG_DIR/${AGENT_NAME}_status"
echo "RUNNING" > "$STATUS_FILE"
echo "$END_TIME" >> "$STATUS_FILE"
echo "$FINDINGS_FILE" >> "$STATUS_FILE"

# Monitor loop - runs in background
(
    while [ $(date +%s) -lt $END_TIME ]; do
        remaining=$(get_remaining)
        echo "[$(date +%H:%M:%S)] Time remaining: $(format_time $remaining)" >> "$LOG_FILE"
        sleep 300  # Log every 5 minutes
    done
    echo "COMPLETED" > "$STATUS_FILE"
    echo "[$(date)] Session complete!" >> "$LOG_FILE"
) &

MONITOR_PID=$!

# Output session info for the agent
echo ""
echo "TIME_REMAINING_SECONDS=$DURATION_SECONDS"
echo "FINDINGS_FILE=$FINDINGS_FILE"
echo "LOG_FILE=$LOG_FILE"
echo "END_TIME=$END_TIME"
echo ""
echo "Agent must work until: $(date -d @$END_TIME)"
echo ""

# Keep this script running until time is up (agent process attaches to this)
wait $MONITOR_PID 2>/dev/null || true
