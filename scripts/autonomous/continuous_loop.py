#!/usr/bin/env python3
"""
Continuous Autonomous Development Loop

This script runs indefinitely, cycling through:
1. Implementation - Fix issues from the audit
2. Testing - Run tests, fix failures
3. Review - Analyze what was done, plan next
4. Repeat

Stops only when user sends a message (creates STOP file) or explicit kill.
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
STATUS_FILE = LOG_DIR / "continuous_status.json"
CYCLE_LOG = LOG_DIR / "continuous_loop.log"

# Session durations (hours)
IMPLEMENT_HOURS = 4
TEST_HOURS = 2
REVIEW_HOURS = 1

def log(msg: str):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(CYCLE_LOG, 'a') as f:
        f.write(line + "\n")

def should_stop() -> bool:
    """Check if we should stop."""
    return STOP_FILE.exists()

def update_status(phase: str, cycle: int, details: dict = None):
    """Update status file for monitoring."""
    status = {
        "phase": phase,
        "cycle": cycle,
        "updated": datetime.now().isoformat(),
        "details": details or {}
    }
    with open(STATUS_FILE, 'w') as f:
        json.dump(status, f, indent=2)

def run_claude_session(prompt: str, output_file: str, hours: float) -> bool:
    """Run a Claude session and wait for completion."""
    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--output-format", "text",
        "--model", "opus",
        prompt
    ]

    log(f"Starting {hours}h session -> {output_file}")

    try:
        with open(output_file, 'w') as f:
            process = subprocess.Popen(
                cmd,
                cwd=str(WORK_DIR),
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True
            )

        # Wait with timeout (hours + 30min buffer)
        timeout_seconds = int((hours + 0.5) * 3600)
        start = time.time()

        while process.poll() is None:
            if should_stop():
                log("Stop requested, terminating session")
                process.terminate()
                return False

            if time.time() - start > timeout_seconds:
                log("Session timeout, terminating")
                process.terminate()
                break

            time.sleep(60)  # Check every minute

        return process.returncode == 0

    except Exception as e:
        log(f"Session error: {e}")
        return False

def get_implementation_prompt(cycle: int) -> str:
    """Generate implementation prompt based on cycle."""

    # Read previous session results if they exist
    prev_results = ""
    prev_file = LOG_DIR / f"implementation_cycle_{cycle-1}.md"
    if prev_file.exists():
        prev_results = f"\n\nPREVIOUS SESSION RESULTS:\n{prev_file.read_text()[:5000]}"

    return f"""# CONTINUOUS IMPLEMENTATION SESSION - CYCLE {cycle}

You are in an autonomous development loop working on Blackreach. The human is sleeping.
DO NOT ask questions. DO NOT stop early. Work until the session ends.

## CONTEXT
- This is cycle {cycle} of continuous development
- Previous work has been done - build on it, don't repeat
- Focus on making REAL CODE CHANGES
- Run tests after changes to verify they work
{prev_results}

## YOUR MISSION

Work through these priorities in order:

### If Security Issues Remain:
1. Check cookie_manager.py - is PBKDF2 salt properly randomized?
2. Check browser.py - is SSL verification configurable?
3. Check browser.py - are download filenames sanitized?
4. Check browser.py - is there SSRF protection?
5. Add any missing security fixes

### If Performance Issues Remain:
1. Check __init__.py - are imports lazy?
2. Check parallel_ops.py - does ParallelFetcher use ThreadPoolExecutor?
3. Check memory.py - are there database indexes?
4. Check observer.py - is lxml being used?

### If Architecture Issues Remain:
1. Add context manager to Hand class if missing
2. Pre-compile regex patterns at module level
3. Add type hints to public functions

### If Tests Are Needed:
1. Write tests for any untested critical classes
2. Run pytest to verify nothing is broken
3. Fix any test failures

## OUTPUT

Write all work to: {LOG_DIR}/implementation_cycle_{cycle}.md

Use this format:
```markdown
# Cycle {cycle} Implementation Log

## Changes Made
1. [Description of change] - [file:line]
2. ...

## Tests Run
- pytest result: PASS/FAIL
- Failures fixed: [list]

## Next Priorities
- [What should be done next cycle]
```

## RULES
- Make real code changes with the Edit tool
- Run tests with: pytest tests/ -x --tb=short
- Don't repeat work from previous cycles
- Document everything you do
- Keep working until session timeout

BEGIN NOW.
"""

def get_test_prompt(cycle: int) -> str:
    """Generate testing prompt."""
    return f"""# TEST AND FIX SESSION - CYCLE {cycle}

You are in an autonomous testing loop. The human is sleeping.
DO NOT ask questions. Fix any issues you find.

## YOUR MISSION

1. Run the full test suite:
   pytest tests/ -v --tb=short

2. For each failure:
   - Read the failing test
   - Read the code being tested
   - Determine if it's a test bug or code bug
   - Fix the appropriate file

3. Run tests again to verify fixes

4. If all tests pass, look for:
   - Tests with weak assertions (just checking existence)
   - Missing edge case tests
   - Untested code paths

## OUTPUT

Write to: {LOG_DIR}/test_cycle_{cycle}.md

Format:
```markdown
# Test Cycle {cycle}

## Initial Test Run
- Total: X
- Passed: X
- Failed: X

## Failures Fixed
1. [test name] - [what was wrong] - [how fixed]

## Final Test Run
- Total: X
- Passed: X
- Failed: X

## Test Improvements Made
- [new tests added or assertions strengthened]
```

BEGIN NOW.
"""

def get_review_prompt(cycle: int) -> str:
    """Generate review/planning prompt."""

    # Gather context from this cycle
    impl_file = LOG_DIR / f"implementation_cycle_{cycle}.md"
    test_file = LOG_DIR / f"test_cycle_{cycle}.md"

    impl_content = impl_file.read_text()[:3000] if impl_file.exists() else "No implementation log"
    test_content = test_file.read_text()[:2000] if test_file.exists() else "No test log"

    return f"""# REVIEW AND PLAN SESSION - CYCLE {cycle}

You are reviewing the work done in cycle {cycle}. The human is sleeping.
Analyze what was accomplished and plan the next cycle.

## IMPLEMENTATION LOG
{impl_content}

## TEST LOG
{test_content}

## YOUR MISSION

1. Review what was accomplished:
   - What security fixes were made?
   - What performance improvements?
   - What tests were added/fixed?

2. Check the codebase for:
   - Any regressions introduced
   - Incomplete changes
   - New issues created by fixes

3. Plan next cycle priorities:
   - What's most important to do next?
   - What was missed or incomplete?
   - Any new issues discovered?

4. Update the improvement plan in Obsidian if significant progress was made

## OUTPUT

Write to: {LOG_DIR}/review_cycle_{cycle}.md

Format:
```markdown
# Cycle {cycle} Review

## Accomplished
- [list of completed items]

## Issues Found
- [any problems discovered]

## Next Cycle Priorities
1. [highest priority]
2. [second priority]
3. [third priority]

## Overall Progress
- Security: X% complete
- Performance: X% complete
- Architecture: X% complete
- Testing: X% complete
```

BEGIN NOW.
"""

def run_cycle(cycle: int) -> bool:
    """Run one full development cycle."""

    log(f"{'='*60}")
    log(f"STARTING CYCLE {cycle}")
    log(f"{'='*60}")

    # Phase 1: Implementation
    update_status("implementation", cycle, {"hours": IMPLEMENT_HOURS})
    log(f"Phase 1: Implementation ({IMPLEMENT_HOURS}h)")

    if should_stop():
        return False

    run_claude_session(
        get_implementation_prompt(cycle),
        str(LOG_DIR / f"implementation_cycle_{cycle}.md"),
        IMPLEMENT_HOURS
    )

    # Phase 2: Testing
    update_status("testing", cycle, {"hours": TEST_HOURS})
    log(f"Phase 2: Testing ({TEST_HOURS}h)")

    if should_stop():
        return False

    run_claude_session(
        get_test_prompt(cycle),
        str(LOG_DIR / f"test_cycle_{cycle}.md"),
        TEST_HOURS
    )

    # Phase 3: Review
    update_status("review", cycle, {"hours": REVIEW_HOURS})
    log(f"Phase 3: Review ({REVIEW_HOURS}h)")

    if should_stop():
        return False

    run_claude_session(
        get_review_prompt(cycle),
        str(LOG_DIR / f"review_cycle_{cycle}.md"),
        REVIEW_HOURS
    )

    log(f"CYCLE {cycle} COMPLETE")
    return True

def main():
    """Main continuous loop."""

    # Setup
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stop file if exists
    if STOP_FILE.exists():
        STOP_FILE.unlink()

    log("="*60)
    log("CONTINUOUS AUTONOMOUS DEVELOPMENT LOOP STARTED")
    log(f"Work directory: {WORK_DIR}")
    log(f"To stop: touch {STOP_FILE}")
    log("="*60)

    cycle = 1

    # Check for previous cycles
    existing = list(LOG_DIR.glob("implementation_cycle_*.md"))
    if existing:
        cycle = max(int(f.stem.split('_')[-1]) for f in existing) + 1
        log(f"Resuming from cycle {cycle}")

    # Main loop - runs forever until stopped
    while not should_stop():
        try:
            success = run_cycle(cycle)
            if not success:
                log("Cycle interrupted")
                break

            cycle += 1

            # Brief pause between cycles
            log("Cycle complete, starting next in 60 seconds...")
            for _ in range(60):
                if should_stop():
                    break
                time.sleep(1)

        except KeyboardInterrupt:
            log("Keyboard interrupt received")
            break

        except Exception as e:
            log(f"Error in cycle {cycle}: {e}")
            # Don't crash, try next cycle
            time.sleep(300)
            cycle += 1

    log("="*60)
    log("CONTINUOUS LOOP STOPPED")
    log(f"Completed {cycle - 1} cycles")
    log("="*60)

    # Final status
    update_status("stopped", cycle - 1, {"reason": "user_stop" if should_stop() else "error"})

if __name__ == "__main__":
    main()
