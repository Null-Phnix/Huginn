#!/usr/bin/env python3
"""
Smart Autonomous Development Loop

The perfect dev loop:
1. Work session runs (2-4 hours)
2. Claude wakes up, reviews what was done
3. Claude thinks about project state, decides what's next
4. Claude launches new session with specific focus
5. Repeat forever

No fixed phases - Claude adapts based on actual project needs.
"""

import os
import sys
import time
import subprocess
import json
from pathlib import Path
from datetime import datetime, timedelta
import signal

# Configuration
WORK_DIR = Path("/mnt/AI_Projects/Blackreach")
LOG_DIR = WORK_DIR / "deep_work_logs"
STOP_FILE = LOG_DIR / "STOP"
STATUS_FILE = LOG_DIR / "smart_status.json"
LOOP_LOG = LOG_DIR / "smart_loop.log"
DECISIONS_LOG = LOG_DIR / "decisions.log"

# Session duration (hours) - shorter sessions, more frequent reviews
WORK_HOURS = 3
REVIEW_MINUTES = 15

def log(msg: str):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOOP_LOG, 'a') as f:
        f.write(line + "\n")

def should_stop() -> bool:
    """Check if we should stop."""
    return STOP_FILE.exists()

def update_status(phase: str, cycle: int, task: str = "", details: dict = None):
    """Update status file."""
    status = {
        "phase": phase,
        "cycle": cycle,
        "current_task": task,
        "updated": datetime.now().isoformat(),
        "details": details or {}
    }
    with open(STATUS_FILE, 'w') as f:
        json.dump(status, f, indent=2)

def get_recent_work_summary() -> str:
    """Get summary of recent work for Claude to review."""
    summaries = []

    # Get last few cycle logs
    logs = sorted(LOG_DIR.glob("work_cycle_*.md"), reverse=True)[:3]
    for log_file in logs:
        content = log_file.read_text()[:3000]
        summaries.append(f"=== {log_file.name} ===\n{content}\n")

    # Get test results if exists
    test_log = LOG_DIR / "latest_tests.txt"
    if test_log.exists():
        summaries.append(f"=== Latest Test Results ===\n{test_log.read_text()[:1000]}\n")

    return "\n".join(summaries) if summaries else "No previous work logs found."

def run_decision_session() -> dict:
    """
    Run a short Claude session to decide what to work on next.
    Returns dict with 'task' and 'focus' keys.
    """
    log("Running decision session - Claude thinking about what's next...")

    recent_work = get_recent_work_summary()

    prompt = f"""# BLACKREACH PROJECT - DECISION SESSION

You are reviewing the Blackreach project to decide what to work on next.
This is a quick decision session - analyze and decide in under 15 minutes.

## RECENT WORK COMPLETED
{recent_work}

## PROJECT LOCATION
/mnt/AI_Projects/Blackreach

## YOUR TASK

1. Quickly assess the current project state
2. Check what's been done vs what still needs work
3. Identify the SINGLE most valuable thing to work on next

Consider:
- Security issues (highest priority if any remain)
- Failing tests (must fix before new features)
- Test coverage gaps (important for stability)
- Performance issues
- Code quality / refactoring
- New features / enhancements
- Documentation

## OUTPUT FORMAT

Write your decision to: {LOG_DIR}/next_task.json

Format:
```json
{{
    "task": "Brief task title",
    "focus": "security|testing|performance|architecture|features|docs",
    "description": "Detailed description of what to do",
    "files": ["list", "of", "relevant", "files"],
    "estimated_hours": 2-4,
    "priority": "critical|high|medium|low",
    "reasoning": "Why this is the most important thing to do next"
}}
```

Be specific. The next work session will use this to know exactly what to do.

After writing the JSON, confirm by outputting: DECISION COMPLETE
"""

    output_file = LOG_DIR / "decision_output.txt"

    try:
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--model", "opus",
            prompt
        ]

        with open(output_file, 'w') as f:
            process = subprocess.Popen(
                cmd,
                cwd=str(WORK_DIR),
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True
            )

        # Wait with timeout
        timeout = REVIEW_MINUTES * 60
        start = time.time()
        while process.poll() is None:
            if time.time() - start > timeout:
                process.terminate()
                break
            if should_stop():
                process.terminate()
                return None
            time.sleep(10)

        # Read the decision
        task_file = LOG_DIR / "next_task.json"
        if task_file.exists():
            with open(task_file) as f:
                decision = json.load(f)
            log(f"Decision: {decision.get('task', 'Unknown')} [{decision.get('focus', 'general')}]")

            # Log the decision
            with open(DECISIONS_LOG, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{datetime.now().isoformat()}]\n")
                f.write(json.dumps(decision, indent=2))
                f.write("\n")

            return decision
        else:
            log("No decision file created, using default")
            return {
                "task": "General improvement",
                "focus": "testing",
                "description": "Run tests, fix failures, improve coverage",
                "estimated_hours": 3
            }

    except Exception as e:
        log(f"Decision session error: {e}")
        return {
            "task": "Continue development",
            "focus": "general",
            "description": "Continue improving the codebase",
            "estimated_hours": 3
        }

def get_work_prompt(decision: dict, cycle: int) -> str:
    """Generate work prompt based on decision."""

    task = decision.get("task", "General improvement")
    focus = decision.get("focus", "general")
    description = decision.get("description", "Improve the codebase")
    files = decision.get("files", [])
    hours = decision.get("estimated_hours", 3)

    files_section = ""
    if files:
        files_section = f"\n\n## KEY FILES TO FOCUS ON\n" + "\n".join(f"- {f}" for f in files)

    focus_guidance = {
        "security": """
### Security Focus
- Check for vulnerabilities: injection, SSRF, path traversal
- Verify encryption is properly implemented
- Ensure no secrets in code
- Validate all user inputs
- Check authentication/authorization""",
        "testing": """
### Testing Focus
- Run pytest and fix any failures
- Add tests for untested critical paths
- Strengthen weak assertions
- Add edge case tests
- Aim for meaningful coverage, not just numbers""",
        "performance": """
### Performance Focus
- Profile slow operations
- Optimize database queries
- Reduce unnecessary I/O
- Implement caching where helpful
- Check for memory leaks""",
        "architecture": """
### Architecture Focus
- Break up large files/classes
- Improve code organization
- Reduce coupling between modules
- Add proper abstractions
- Clean up technical debt""",
        "features": """
### Feature Focus
- Implement the feature incrementally
- Add tests as you go
- Keep changes minimal and focused
- Document new functionality
- Consider edge cases""",
        "docs": """
### Documentation Focus
- Add missing docstrings
- Update README if needed
- Document complex logic
- Add type hints
- Create usage examples"""
    }

    guidance = focus_guidance.get(focus, "### General Focus\nImprove code quality and fix issues.")

    return f"""# BLACKREACH WORK SESSION - CYCLE {cycle}

You are working autonomously on the Blackreach project. The human is away.
DO NOT ask questions. DO NOT stop early. Work until the session ends.

## YOUR TASK
**{task}**

{description}
{files_section}

{guidance}

## RULES
1. Make REAL code changes using the Edit tool
2. Run tests after changes: `pytest tests/ -x --tb=short`
3. Fix any test failures before moving on
4. Document all changes in your output file
5. Work systematically through the task
6. If you finish early, find related improvements

## OUTPUT
Write all work to: {LOG_DIR}/work_cycle_{cycle}.md

Format:
```markdown
# Cycle {cycle}: {task}

## Summary
[What you accomplished]

## Changes Made
1. [Change description] - [file:line]
2. ...

## Tests
- Ran: [count]
- Passed: [count]
- Fixed: [list any failures fixed]

## Notes for Next Session
[What should be done next]
```

## TIME
You have {hours} hours. Use them wisely.

BEGIN NOW.
"""

def run_work_session(decision: dict, cycle: int) -> bool:
    """Run a work session based on the decision."""

    task = decision.get("task", "General work")
    hours = decision.get("estimated_hours", WORK_HOURS)

    log(f"Starting work session: {task} ({hours}h)")
    update_status("working", cycle, task, {"hours": hours})

    prompt = get_work_prompt(decision, cycle)
    output_file = LOG_DIR / f"work_cycle_{cycle}.md"

    try:
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--model", "opus",
            prompt
        ]

        with open(output_file, 'w') as f:
            # Write header
            f.write(f"# Cycle {cycle}: {task}\n\n")
            f.write(f"Started: {datetime.now().isoformat()}\n\n")
            f.flush()

            process = subprocess.Popen(
                cmd,
                cwd=str(WORK_DIR),
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True
            )

        # Wait with timeout
        timeout_seconds = int((hours + 0.5) * 3600)
        start = time.time()

        while process.poll() is None:
            if should_stop():
                log("Stop requested, terminating session")
                process.terminate()
                return False

            if time.time() - start > timeout_seconds:
                log("Session timeout reached")
                process.terminate()
                break

            time.sleep(60)

        # Append completion note
        with open(output_file, 'a') as f:
            f.write(f"\n\nCompleted: {datetime.now().isoformat()}\n")

        return True

    except Exception as e:
        log(f"Work session error: {e}")
        return False

def run_quick_test_check():
    """Run a quick test to capture current state."""
    log("Running quick test check...")
    try:
        result = subprocess.run(
            ["pytest", "tests/", "-q", "--tb=no", "-x", "--maxfail=3"],
            cwd=str(WORK_DIR),
            capture_output=True,
            text=True,
            timeout=120  # 2 minute max
        )

        output = result.stdout + result.stderr
        with open(LOG_DIR / "latest_tests.txt", 'w') as f:
            f.write(output)

        # Extract pass/fail counts
        if "passed" in output:
            log(f"Tests: {output.split('passed')[0].split()[-1]} passed")

    except Exception as e:
        log(f"Test check failed: {e}")

def main():
    """Main smart loop."""

    # Setup
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if STOP_FILE.exists():
        STOP_FILE.unlink()

    log("=" * 60)
    log("SMART AUTONOMOUS LOOP STARTED")
    log(f"Project: {WORK_DIR}")
    log(f"To stop: touch {STOP_FILE}")
    log("=" * 60)

    cycle = 1

    # Find highest existing cycle
    existing = list(LOG_DIR.glob("work_cycle_*.md"))
    if existing:
        cycle = max(int(f.stem.split('_')[-1]) for f in existing) + 1
        log(f"Resuming from cycle {cycle}")

    # Main loop
    while not should_stop():
        try:
            log(f"\n{'='*60}")
            log(f"CYCLE {cycle}")
            log(f"{'='*60}")

            # Phase 1: Quick test check
            run_quick_test_check()

            # Phase 2: Decision - Claude thinks about what's next
            update_status("deciding", cycle)
            decision = run_decision_session()

            if decision is None or should_stop():
                break

            # Phase 3: Work session based on decision
            success = run_work_session(decision, cycle)

            if not success or should_stop():
                break

            log(f"CYCLE {cycle} COMPLETE")
            cycle += 1

            # Brief pause between cycles
            log("Starting next cycle in 30 seconds...")
            for _ in range(30):
                if should_stop():
                    break
                time.sleep(1)

        except KeyboardInterrupt:
            log("Keyboard interrupt")
            break

        except Exception as e:
            log(f"Cycle error: {e}")
            time.sleep(60)
            cycle += 1

    log("=" * 60)
    log("SMART LOOP STOPPED")
    log(f"Completed {cycle - 1} cycles")
    log("=" * 60)

    update_status("stopped", cycle - 1)

if __name__ == "__main__":
    main()
