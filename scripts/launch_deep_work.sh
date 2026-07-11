#!/bin/bash
# Launch Deep Work Sprint - Autonomous agent exploration
# Usage: ./launch_deep_work.sh [hours_per_agent] [agents...]
# Example: ./launch_deep_work.sh 2 performance security ux
# Example: ./launch_deep_work.sh 1 all  (runs all agents for 1 hour each)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="/mnt/AI_Projects/Blackreach"
LOG_DIR="$WORK_DIR/deep_work_logs"
PROMPT_DIR="$SCRIPT_DIR/agent_prompts"

HOURS="${1:-2}"
shift || true

# If no agents specified or "all", run all agents
if [ -z "$1" ] || [ "$1" = "all" ]; then
    AGENTS=("performance" "security" "ux" "code" "test")
else
    AGENTS=("$@")
fi

mkdir -p "$LOG_DIR"

# Map agent names to prompt files
declare -A AGENT_PROMPTS=(
    ["performance"]="performance_hunter.md"
    ["security"]="security_auditor.md"
    ["ux"]="ux_investigator.md"
    ["code"]="code_archeologist.md"
    ["test"]="test_saboteur.md"
)

declare -A AGENT_NAMES=(
    ["performance"]="Performance Hunter"
    ["security"]="Security Auditor"
    ["ux"]="UX Investigator"
    ["code"]="Code Archeologist"
    ["test"]="Test Saboteur"
)

echo "=========================================="
echo "   BLACKREACH DEEP WORK SPRINT"
echo "=========================================="
echo "Duration per agent: ${HOURS} hours"
echo "Agents to launch: ${AGENTS[*]}"
echo "Log directory: $LOG_DIR"
echo "Started: $(date)"
echo "=========================================="
echo ""

# Create master status file
MASTER_STATUS="$LOG_DIR/sprint_status.json"
cat > "$MASTER_STATUS" << EOF
{
    "started": "$(date -Iseconds)",
    "hours_per_agent": $HOURS,
    "agents": [$(printf '"%s",' "${AGENTS[@]}" | sed 's/,$//')]
}
EOF

# Function to launch an agent
launch_agent() {
    local agent_key="$1"
    local hours="$2"
    local prompt_file="${PROMPT_DIR}/${AGENT_PROMPTS[$agent_key]}"
    local agent_name="${AGENT_NAMES[$agent_key]}"
    local findings_file="$LOG_DIR/${agent_key}_findings.md"
    local log_file="$LOG_DIR/${agent_key}_$(date +%Y%m%d_%H%M%S).log"

    if [ ! -f "$prompt_file" ]; then
        echo "ERROR: Prompt file not found: $prompt_file"
        return 1
    fi

    # Calculate end time
    local duration_seconds=$((hours * 3600))
    local end_time=$(($(date +%s) + duration_seconds))

    # Read the prompt
    local prompt=$(cat "$prompt_file")

    # Create the agent instruction with time enforcement
    local full_prompt=$(cat << ENDPROMPT
# MANDATORY TIME REQUIREMENT

You MUST work on this task for AT LEAST ${hours} hours. This is non-negotiable.

Your session end time: $(date -d @$end_time)
Current time: $(date)

You will continuously work and log findings until time expires. Do NOT finish early.

## How to Track Time
- Check the clock periodically
- You cannot complete this task before the end time
- If you run out of things to do, go deeper - there's always more to find

## Findings Log
Write ALL findings to: $findings_file
Update this file continuously as you work.

## Working Directory
$WORK_DIR

---

$prompt

---

# BEGIN WORK NOW

Start by reading the codebase structure, then systematically work through your checklist.
Log your first finding within 5 minutes to prove you're working.
Continue until $(date -d @$end_time).
ENDPROMPT
)

    echo "Launching: $agent_name"
    echo "  Prompt: $prompt_file"
    echo "  Findings: $findings_file"
    echo "  Duration: ${hours}h (until $(date -d @$end_time '+%H:%M:%S'))"
    echo "  Log: $log_file"

    # Initialize findings file
    cat > "$findings_file" << EOF
# $agent_name - Deep Work Findings

**Session Started:** $(date)
**Required Duration:** ${hours} hours
**Must Complete By:** $(date -d @$end_time)
**Working Directory:** $WORK_DIR

---

## Findings

EOF

    # Launch the agent (this would integrate with claude-code or similar)
    # For now, output the command that would be run
    echo ""
    echo "  Command to run:"
    echo "  claude --print \"$full_prompt\" 2>&1 | tee -a \"$log_file\""
    echo ""

    # Create a launcher script for this specific agent
    local agent_launcher="$LOG_DIR/run_${agent_key}.sh"
    cat > "$agent_launcher" << LAUNCHER
#!/bin/bash
# Auto-generated launcher for $agent_name
# Run with: bash $agent_launcher

cd "$WORK_DIR"

END_TIME=$end_time
FINDINGS_FILE="$findings_file"
LOG_FILE="$log_file"

echo "Starting $agent_name..."
echo "Must work until: \$(date -d @\$END_TIME)"
echo "Findings will be in: \$FINDINGS_FILE"
echo ""

# The actual claude command - adjust based on your setup
# Option 1: Interactive mode
# claude

# Option 2: With initial prompt from file
# claude --prompt-file "$PROMPT_DIR/${AGENT_PROMPTS[$agent_key]}"

# Option 3: Piped prompt
cat << 'PROMPT' | claude
$full_prompt
PROMPT
LAUNCHER
    chmod +x "$agent_launcher"
}

# Launch each agent
for agent in "${AGENTS[@]}"; do
    if [ -n "${AGENT_PROMPTS[$agent]}" ]; then
        launch_agent "$agent" "$HOURS"
    else
        echo "WARNING: Unknown agent type: $agent"
        echo "  Valid types: performance, security, ux, code, test"
    fi
done

echo ""
echo "=========================================="
echo "Agent launchers created in: $LOG_DIR"
echo ""
echo "To run agents in parallel terminals:"
for agent in "${AGENTS[@]}"; do
    echo "  Terminal $agent: bash $LOG_DIR/run_${agent}.sh"
done
echo ""
echo "To monitor findings:"
echo "  tail -f $LOG_DIR/*_findings.md"
echo ""
echo "To check all status:"
echo "  ls -la $LOG_DIR/"
echo "=========================================="
