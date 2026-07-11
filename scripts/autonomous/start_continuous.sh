#!/bin/bash
# Start continuous autonomous development loop

WORK_DIR="/mnt/AI_Projects/Blackreach"
LOG_DIR="$WORK_DIR/deep_work_logs"

echo "=========================================="
echo "  CONTINUOUS AUTONOMOUS DEVELOPMENT"
echo "=========================================="
echo ""
echo "This will run INDEFINITELY until you stop it."
echo ""
echo "To stop: touch $LOG_DIR/STOP"
echo "Or:      pkill -f continuous_loop.py"
echo ""
echo "Monitor: tail -f $LOG_DIR/continuous_loop.log"
echo "Status:  cat $LOG_DIR/continuous_status.json"
echo ""
echo "=========================================="

# Kill any existing daemon
pkill -f "daemon.py" 2>/dev/null
pkill -f "continuous_loop.py" 2>/dev/null
sleep 2

# Clear stop file if exists
rm -f "$LOG_DIR/STOP"

# Start continuous loop
cd "$WORK_DIR"
nohup python3 scripts/autonomous/continuous_loop.py > "$LOG_DIR/continuous_stdout.log" 2>&1 &
echo $! > "$LOG_DIR/continuous.pid"

echo "Started with PID: $(cat $LOG_DIR/continuous.pid)"
echo ""
echo "Go sleep. I'll keep working."
echo ""
