#!/usr/bin/env python3
"""
Auto-Transition Script

Monitors the smart_loop and automatically transitions to user testing mode
after a specified cycle completes.

Usage:
    python auto_transition.py                    # Transition after next cycle
    python auto_transition.py --after-cycle 10   # Transition after cycle 10
    python auto_transition.py --after-hours 2    # Transition after 2 more hours
"""

import subprocess
import time
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
import argparse

# Paths
PROJECT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_DIR / "deep_work_logs"
STOP_FILE = LOG_DIR / "STOP"
STATUS_FILE = LOG_DIR / "smart_status.json"
LOOP_LOG = LOG_DIR / "smart_loop.log"
USER_TEST_LOG = LOG_DIR / "user_testing.log"
USER_TEST_FINDINGS = LOG_DIR / "user_test_findings.md"

# Test scenarios for user testing
USER_TEST_SCENARIOS = [
    {
        "name": "ArXiv Paper Download",
        "goal": "go to arxiv.org, search for 'transformer architecture', and download 2 papers",
        "expected": ["pdf download", "navigation to arxiv"],
        "timeout": 300
    },
    {
        "name": "Wikipedia Research",
        "goal": "search wikipedia for 'machine learning' and find information about neural networks",
        "expected": ["wikipedia navigation", "content extraction"],
        "timeout": 180
    },
    {
        "name": "GitHub README Fetch",
        "goal": "go to github.com/python/cpython and read the README",
        "expected": ["github navigation", "readme content"],
        "timeout": 180
    },
    {
        "name": "Image Search",
        "goal": "find and download 2 landscape images from unsplash",
        "expected": ["image download", "unsplash navigation"],
        "timeout": 300
    },
    {
        "name": "Session Resume Test",
        "goal": "go to arxiv.org and search for 'deep learning'",
        "expected": ["session save on interrupt", "resume capability"],
        "timeout": 120,
        "interrupt_after": 30  # Interrupt after 30 seconds to test resume
    },
    {
        "name": "Multi-Page Navigation",
        "goal": "go to arxiv.org, search for 'neural network', and browse through 3 pages of results",
        "expected": ["pagination detection", "multi-page navigation"],
        "timeout": 240
    },
    {
        "name": "Error Recovery",
        "goal": "go to httpstat.us/500 then recover and go to wikipedia.org",
        "expected": ["error handling", "recovery navigation"],
        "timeout": 180
    },
    {
        "name": "Form Interaction",
        "goal": "go to google.com and search for 'blackreach browser agent'",
        "expected": ["form fill", "search submission"],
        "timeout": 120
    }
]


def log(msg: str):
    """Log with timestamp to both console and file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(USER_TEST_LOG, "a") as f:
        f.write(line + "\n")


def get_current_cycle() -> int:
    """Get the current cycle number from status file."""
    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
            return data.get("cycle", 0)
    except:
        return 0


def get_loop_phase() -> str:
    """Get current phase from status file."""
    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
            return data.get("phase", "unknown")
    except:
        return "unknown"


def wait_for_cycle_complete(target_cycle: int):
    """Wait until the target cycle is complete."""
    log(f"Waiting for cycle {target_cycle} to complete...")

    while True:
        current = get_current_cycle()
        phase = get_loop_phase()

        # Check if we've moved past the target cycle
        if current > target_cycle:
            log(f"Cycle {target_cycle} complete, now on cycle {current}")
            return True

        # Check loop log for completion
        if LOOP_LOG.exists():
            with open(LOOP_LOG) as f:
                content = f.read()
                if f"CYCLE {target_cycle} COMPLETE" in content:
                    log(f"Cycle {target_cycle} marked complete in log")
                    return True

        log(f"Current: cycle {current}, phase: {phase} - checking again in 60s")
        time.sleep(60)


def wait_for_hours(hours: float):
    """Wait for specified hours then transition."""
    end_time = datetime.now() + timedelta(hours=hours)
    log(f"Will transition at {end_time.strftime('%Y-%m-%d %H:%M:%S')} ({hours}h from now)")

    while datetime.now() < end_time:
        remaining = (end_time - datetime.now()).total_seconds() / 60
        log(f"Transition in {remaining:.0f} minutes...")
        time.sleep(300)  # Check every 5 min

    return True


def stop_smart_loop():
    """Stop the smart loop by creating STOP file."""
    log("Creating STOP file to halt smart_loop...")
    STOP_FILE.touch()

    # Wait for loop to actually stop (check for process)
    log("Waiting for smart_loop to stop...")
    for _ in range(60):  # Wait up to 5 minutes
        result = subprocess.run(
            ["pgrep", "-f", "smart_loop.py"],
            capture_output=True
        )
        if result.returncode != 0:
            log("smart_loop stopped")
            return True
        time.sleep(5)

    log("Warning: smart_loop may still be running")
    return False


def run_user_test(scenario: dict) -> dict:
    """Run a single user test scenario with visible browser."""
    log(f"\n{'='*60}")
    log(f"TESTING: {scenario['name']}")
    log(f"Goal: {scenario['goal']}")
    log(f"{'='*60}")

    result = {
        "scenario": scenario["name"],
        "goal": scenario["goal"],
        "start_time": datetime.now().isoformat(),
        "success": False,
        "errors": [],
        "observations": [],
        "duration": 0
    }

    start = time.time()

    try:
        # Run blackreach with visible browser (no --headless)
        cmd = [
            "python", "-m", "blackreach.cli",
            "run", scenario["goal"],
            "--steps", "30"  # Limit steps for testing
        ]

        # Add interrupt handling for resume test
        timeout = scenario.get("timeout", 180)
        interrupt_after = scenario.get("interrupt_after")

        if interrupt_after:
            # Start process, interrupt, then try resume
            log(f"Will interrupt after {interrupt_after}s to test resume...")
            proc = subprocess.Popen(
                cmd,
                cwd=PROJECT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            time.sleep(interrupt_after)
            proc.send_signal(2)  # SIGINT
            proc.wait(timeout=10)

            result["observations"].append(f"Interrupted after {interrupt_after}s")

            # Try to resume
            log("Attempting resume...")
            resume_cmd = [
                "python", "-m", "blackreach.cli",
                "sessions"
            ]
            resume_result = subprocess.run(
                resume_cmd,
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                timeout=30
            )
            result["observations"].append(f"Sessions output: {resume_result.stdout[:500]}")

        else:
            # Normal run
            proc_result = subprocess.run(
                cmd,
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            result["stdout"] = proc_result.stdout[-2000:] if proc_result.stdout else ""
            result["stderr"] = proc_result.stderr[-1000:] if proc_result.stderr else ""
            result["return_code"] = proc_result.returncode

            if proc_result.returncode == 0:
                result["success"] = True
                log("✓ Test completed successfully")
            else:
                result["errors"].append(f"Exit code: {proc_result.returncode}")
                log(f"✗ Test failed with exit code {proc_result.returncode}")

    except subprocess.TimeoutExpired:
        result["errors"].append(f"Timeout after {timeout}s")
        log(f"✗ Test timed out after {timeout}s")
    except Exception as e:
        result["errors"].append(str(e))
        log(f"✗ Test error: {e}")

    result["duration"] = time.time() - start
    result["end_time"] = datetime.now().isoformat()

    return result


def run_all_user_tests() -> list:
    """Run all user test scenarios."""
    log("\n" + "="*60)
    log("STARTING USER TESTING PHASE")
    log("="*60)
    log(f"Running {len(USER_TEST_SCENARIOS)} test scenarios")
    log("Browser will be VISIBLE for observation")
    log("="*60 + "\n")

    results = []

    for i, scenario in enumerate(USER_TEST_SCENARIOS, 1):
        log(f"\n[{i}/{len(USER_TEST_SCENARIOS)}] Starting: {scenario['name']}")

        result = run_user_test(scenario)
        results.append(result)

        # Brief pause between tests
        if i < len(USER_TEST_SCENARIOS):
            log("Pausing 10s before next test...")
            time.sleep(10)

    return results


def generate_findings_report(results: list):
    """Generate a markdown report of findings."""
    log("\nGenerating findings report...")

    passed = sum(1 for r in results if r["success"])
    failed = len(results) - passed

    report = f"""# User Testing Findings

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Tests Run:** {len(results)}
**Passed:** {passed}
**Failed:** {failed}

---

## Summary

| Scenario | Status | Duration | Notes |
|----------|--------|----------|-------|
"""

    for r in results:
        status = "✓ Pass" if r["success"] else "✗ Fail"
        duration = f"{r['duration']:.1f}s"
        notes = "; ".join(r["errors"][:2]) if r["errors"] else "OK"
        report += f"| {r['scenario']} | {status} | {duration} | {notes} |\n"

    report += "\n---\n\n## Detailed Results\n\n"

    for r in results:
        status = "✓ PASSED" if r["success"] else "✗ FAILED"
        report += f"""### {r['scenario']} - {status}

**Goal:** {r['goal']}
**Duration:** {r['duration']:.1f}s
**Time:** {r['start_time']} to {r['end_time']}

"""
        if r["errors"]:
            report += "**Errors:**\n"
            for err in r["errors"]:
                report += f"- {err}\n"
            report += "\n"

        if r.get("observations"):
            report += "**Observations:**\n"
            for obs in r["observations"]:
                report += f"- {obs}\n"
            report += "\n"

        if r.get("stderr") and not r["success"]:
            report += f"**Stderr (last 500 chars):**\n```\n{r['stderr'][-500:]}\n```\n\n"

        report += "---\n\n"

    # Add recommendations section
    report += """## Recommendations for Next Loop

Based on user testing findings, prioritize:

"""

    failed_scenarios = [r for r in results if not r["success"]]
    if failed_scenarios:
        report += "### Failed Scenarios to Fix\n\n"
        for r in failed_scenarios:
            report += f"1. **{r['scenario']}**: {'; '.join(r['errors'][:2])}\n"
    else:
        report += "All scenarios passed! Consider:\n"
        report += "1. Adding more complex test scenarios\n"
        report += "2. Testing edge cases\n"
        report += "3. Performance optimization\n"

    with open(USER_TEST_FINDINGS, "w") as f:
        f.write(report)

    log(f"Report saved to: {USER_TEST_FINDINGS}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Auto-transition from dev loop to user testing")
    parser.add_argument("--after-cycle", type=int, help="Transition after this cycle completes")
    parser.add_argument("--after-hours", type=float, help="Transition after this many hours")
    parser.add_argument("--skip-wait", action="store_true", help="Skip waiting, transition immediately")
    args = parser.parse_args()

    log("="*60)
    log("AUTO-TRANSITION SCRIPT STARTED")
    log(f"Project: {PROJECT_DIR}")
    log("="*60)

    current_cycle = get_current_cycle()
    log(f"Current cycle: {current_cycle}")

    # Determine transition point
    if args.skip_wait:
        log("Skipping wait, transitioning immediately")
    elif args.after_hours:
        wait_for_hours(args.after_hours)
    elif args.after_cycle:
        wait_for_cycle_complete(args.after_cycle)
    else:
        # Default: wait for current cycle + 1 to complete
        target = current_cycle + 1
        log(f"Will transition after cycle {target} completes")
        wait_for_cycle_complete(target)

    # Stop the smart loop
    stop_smart_loop()

    # Give things time to settle
    log("Waiting 30s for processes to clean up...")
    time.sleep(30)

    # Run user tests
    results = run_all_user_tests()

    # Generate report
    report = generate_findings_report(results)

    # Summary
    passed = sum(1 for r in results if r["success"])
    log("\n" + "="*60)
    log("USER TESTING COMPLETE")
    log(f"Results: {passed}/{len(results)} passed")
    log(f"Findings: {USER_TEST_FINDINGS}")
    log("="*60)

    # Clean up STOP file so loop can restart if needed
    if STOP_FILE.exists():
        STOP_FILE.unlink()
        log("Removed STOP file")

    log("\nNext steps:")
    log("1. Review findings at: " + str(USER_TEST_FINDINGS))
    log("2. To restart dev loop: python scripts/autonomous/smart_loop.py")
    log("3. Or manually address issues found")


if __name__ == "__main__":
    main()
