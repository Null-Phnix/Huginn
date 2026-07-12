#!/usr/bin/env python3
"""
Deep Work Orchestrator - Launches autonomous agents for thorough codebase exploration.

This script creates detailed prompts that force agents to work for specified durations,
continuously logging their findings.

Usage:
    python deep_work_orchestrator.py --hours 2 --agents all
    python deep_work_orchestrator.py --hours 1 --agents performance security
"""

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path


WORK_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = WORK_DIR / "deep_work_logs"
PROMPT_DIR = WORK_DIR / "scripts" / "agent_prompts"


AGENT_CONFIG = {
    "performance": {
        "name": "Performance Hunter",
        "prompt_file": "performance_hunter.md",
        "focus": "Find every slow path, memory issue, and optimization opportunity",
        "min_findings": 20,
    },
    "security": {
        "name": "Security Auditor",
        "prompt_file": "security_auditor.md",
        "focus": "Find vulnerabilities, injection points, and security gaps",
        "min_findings": 15,
    },
    "ux": {
        "name": "UX Investigator",
        "prompt_file": "ux_investigator.md",
        "focus": "Use the tool extensively and document every friction point",
        "min_findings": 25,
    },
    "code": {
        "name": "Code Archeologist",
        "prompt_file": "code_archeologist.md",
        "focus": "Review every file for code smells, inconsistencies, and tech debt",
        "min_findings": 30,
    },
    "test": {
        "name": "Test Saboteur",
        "prompt_file": "test_saboteur.md",
        "focus": "Find test gaps, write failing tests, break assumptions",
        "min_findings": 20,
    },
}


def generate_agent_prompt(agent_key: str, hours: float, findings_file: Path) -> str:
    """Generate a comprehensive prompt for an agent with time enforcement."""

    config = AGENT_CONFIG[agent_key]
    prompt_file = PROMPT_DIR / config["prompt_file"]

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")

    base_prompt = prompt_file.read_text()
    end_time = datetime.now() + timedelta(hours=hours)
    duration_minutes = int(hours * 60)

    # Build the comprehensive prompt
    prompt = f"""# AUTONOMOUS DEEP WORK SESSION: {config["name"]}

## CRITICAL TIME REQUIREMENT - READ THIS FIRST

You are in an AUTONOMOUS DEEP WORK SESSION. This means:

1. **YOU MUST WORK FOR {hours} HOURS ({duration_minutes} minutes)**
   - Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
   - Session ends: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
   - You CANNOT finish before this time

2. **MINIMUM {config["min_findings"]} FINDINGS REQUIRED**
   - Log each finding immediately when discovered
   - Quality matters, but so does quantity
   - If you have fewer than {config["min_findings"]} findings, you haven't looked hard enough

3. **CONTINUOUS LOGGING IS MANDATORY**
   - Update the findings file every 5-10 minutes
   - Each update proves you're still working
   - Include timestamps in your entries

4. **NO SHORTCUTS**
   - Don't skim, READ the code
   - Don't assume, VERIFY
   - Don't finish early, DIG DEEPER

## Your Mission
{config["focus"]}

## Findings File
Write all findings to: {findings_file}

Use this exact format for each finding:
```
### [{datetime.now().strftime("%H:%M")}] Finding #N: Title
**Location:** file:line
**Severity:** Critical/High/Medium/Low
**Description:** What you found
**Evidence:** Proof/examples
**Recommendation:** How to fix
---
```

## Working Directory
{WORK_DIR}

## Time Checkpoints
As you work, mentally note these checkpoints:
- 15 min: Should have first 3-5 findings logged
- 30 min: Should have reviewed at least 5 files
- 1 hour: Should have {config["min_findings"] // 2}+ findings
- {hours} hours: Should have {config["min_findings"]}+ findings, session complete

---

{base_prompt}

---

# BEGIN SESSION NOW

Your first action should be to:
1. List all files in the blackreach/ directory
2. Start reading systematically
3. Log your first finding within 10 minutes

Remember: You have {hours} hours. Use every minute. When you think you're done, you're not - go deeper.

START WORKING.
"""
    return prompt


def create_findings_file(agent_key: str) -> Path:
    """Create and initialize a findings file for an agent."""

    config = AGENT_CONFIG[agent_key]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    findings_file = LOG_DIR / f"{agent_key}_findings_{timestamp}.md"

    findings_file.write_text(f"""# {config["name"]} - Deep Work Findings

**Session ID:** {timestamp}
**Started:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Agent Type:** {agent_key}
**Focus:** {config["focus"]}
**Minimum Findings Required:** {config["min_findings"]}

---

## Session Log

""")
    return findings_file


def create_session_manifest(agents: list, hours: float) -> Path:
    """Create a manifest file tracking the entire session."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_file = LOG_DIR / f"session_{timestamp}.json"

    manifest = {
        "session_id": timestamp,
        "started": datetime.now().isoformat(),
        "hours_per_agent": hours,
        "end_time": (datetime.now() + timedelta(hours=hours)).isoformat(),
        "agents": {
            agent: {
                "name": AGENT_CONFIG[agent]["name"],
                "status": "pending",
                "findings_count": 0,
            }
            for agent in agents
        }
    }

    manifest_file.write_text(json.dumps(manifest, indent=2))
    return manifest_file


def print_launch_instructions(agents: list, hours: float, prompts: dict):
    """Print instructions for launching the agents."""

    print("\n" + "=" * 60)
    print("   DEEP WORK SPRINT - READY TO LAUNCH")
    print("=" * 60)
    print(f"\nDuration per agent: {hours} hours")
    print(f"Total agents: {len(agents)}")
    print(f"End time: {(datetime.now() + timedelta(hours=hours)).strftime('%H:%M:%S')}")
    print(f"\nLog directory: {LOG_DIR}")

    print("\n" + "-" * 60)
    print("AGENT PROMPTS GENERATED:")
    print("-" * 60)

    for agent, prompt_file in prompts.items():
        config = AGENT_CONFIG[agent]
        print(f"\n  [{agent.upper()}] {config['name']}")
        print(f"      Prompt: {prompt_file}")
        print(f"      Focus: {config['focus'][:50]}...")
        print(f"      Min findings: {config['min_findings']}")

    print("\n" + "-" * 60)
    print("TO LAUNCH AGENTS:")
    print("-" * 60)
    print("\nOption 1: Run in separate terminals")
    for agent, prompt_file in prompts.items():
        print(f"  claude < {prompt_file}")

    print("\nOption 2: Use Task tool to spawn parallel agents")
    print("  (Copy the prompt content and use Task tool with appropriate agent type)")

    print("\n" + "-" * 60)
    print("TO MONITOR PROGRESS:")
    print("-" * 60)
    print(f"  tail -f {LOG_DIR}/*_findings_*.md")
    print(f"  watch -n 60 'wc -l {LOG_DIR}/*_findings_*.md'")

    print("\n" + "=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Launch deep work agents")
    parser.add_argument("--hours", type=float, default=2.0,
                        help="Hours per agent (default: 2)")
    parser.add_argument("--agents", nargs="+", default=["all"],
                        help="Agents to launch (default: all)")
    args = parser.parse_args()

    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Determine which agents to run
    if "all" in args.agents:
        agents = list(AGENT_CONFIG.keys())
    else:
        agents = [a for a in args.agents if a in AGENT_CONFIG]
        invalid = [a for a in args.agents if a not in AGENT_CONFIG]
        if invalid:
            print(f"Warning: Unknown agents ignored: {invalid}")
            print(f"Valid agents: {list(AGENT_CONFIG.keys())}")

    if not agents:
        print("No valid agents specified!")
        return 1

    # Create session manifest
    manifest = create_session_manifest(agents, args.hours)
    print(f"Session manifest: {manifest}")

    # Generate prompts for each agent
    prompts = {}
    for agent in agents:
        findings_file = create_findings_file(agent)
        prompt = generate_agent_prompt(agent, args.hours, findings_file)

        prompt_file = LOG_DIR / f"{agent}_prompt.md"
        prompt_file.write_text(prompt)
        prompts[agent] = prompt_file

    # Print launch instructions
    print_launch_instructions(agents, args.hours, prompts)

    return 0


if __name__ == "__main__":
    exit(main())
