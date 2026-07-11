#!/usr/bin/env python3
"""
Autonomous Claude Work Daemon

The sauce nobody shares: Force Claude to work for REAL time, not just "finish fast".

How it works:
1. Launch agents with specific tasks
2. Monitor their output files
3. If agent finishes before time expires → resume with "go deeper"
4. Track findings count → if below target, demand more
5. Keep looping until wall-clock time says stop

Usage:
    python daemon.py --hours 10 --task "security audit" --min-findings 100
    python daemon.py --config sprint_config.yaml
"""

import os
import sys
import json
import time
import subprocess
import argparse
import threading
import signal
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
import re


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str
    task_prompt: str
    findings_file: str
    min_findings: int = 50
    hours: float = 2.0
    model: str = "opus"  # opus, sonnet, haiku

    # Runtime state
    agent_id: Optional[str] = None
    status: str = "pending"  # pending, running, completed, resumed
    findings_count: int = 0
    resume_count: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None


@dataclass
class DaemonConfig:
    """Main daemon configuration."""
    work_dir: str
    log_dir: str
    agents: List[AgentConfig] = field(default_factory=list)
    check_interval: int = 60  # seconds between status checks
    max_resumes: int = 20  # max times to resume an agent
    verbose: bool = True


# ============================================================================
# Agent Management
# ============================================================================

class ClaudeAgent:
    """Manages a single Claude Code agent."""

    def __init__(self, config: AgentConfig, work_dir: str, log_dir: str):
        self.config = config
        self.work_dir = Path(work_dir)
        self.log_dir = Path(log_dir)
        self.process: Optional[subprocess.Popen] = None
        self.output_file: Optional[Path] = None

    def launch(self) -> str:
        """Launch the agent and return its ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_file = self.log_dir / f"{self.config.name}_{timestamp}.log"

        # Build the initial prompt
        prompt = self._build_prompt(initial=True)

        # Launch claude in the background
        cmd = [
            "claude",
            "--print",  # Non-interactive mode
            "--dangerously-skip-permissions",  # Allow file edits without prompts
            "--output-format", "text",
            prompt
        ]

        if self.config.model:
            cmd.extend(["--model", self.config.model])

        with open(self.output_file, 'w') as f:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.work_dir),
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True
            )

        self.config.status = "running"
        self.config.start_time = time.time()
        self.config.agent_id = f"agent_{timestamp}"

        return self.config.agent_id

    def resume(self, reason: str) -> bool:
        """Resume the agent with additional instructions."""
        if self.config.resume_count >= 20:  # Safety limit
            return False

        self.config.resume_count += 1

        # Build resume prompt
        prompt = self._build_resume_prompt(reason)

        timestamp = datetime.now().strftime("%H%M%S")
        resume_file = self.log_dir / f"{self.config.name}_resume_{self.config.resume_count}.log"

        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",  # Allow file edits without prompts
            "--output-format", "text",
            prompt
        ]

        if self.config.model:
            cmd.extend(["--model", self.config.model])

        with open(resume_file, 'w') as f:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.work_dir),
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True
            )

        self.config.status = "resumed"
        return True

    def is_running(self) -> bool:
        """Check if agent process is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def get_findings_count(self) -> int:
        """Count findings in the output file."""
        findings_path = Path(self.config.findings_file)
        if not findings_path.exists():
            return 0

        try:
            content = findings_path.read_text()
            # Count markdown headers that indicate findings
            count = len(re.findall(r'^###\s+', content, re.MULTILINE))
            self.config.findings_count = count
            return count
        except Exception:
            return 0

    def time_remaining(self) -> float:
        """Get seconds remaining in this agent's work session."""
        if self.config.start_time is None:
            return self.config.hours * 3600

        elapsed = time.time() - self.config.start_time
        total = self.config.hours * 3600
        return max(0, total - elapsed)

    def _build_prompt(self, initial: bool = True) -> str:
        """Build the prompt for the agent."""
        hours = self.config.hours
        mins = int(hours * 60)
        end_time = datetime.now() + timedelta(hours=hours)

        return f"""# AUTONOMOUS DEEP WORK SESSION

## TIME REQUIREMENT - THIS IS ENFORCED EXTERNALLY
You are being monitored by an external daemon that WILL resume you if you finish early.
- Session duration: {hours} hours ({mins} minutes)
- Session ends: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
- You will be resumed if you complete before this time
- You will be resumed if you have fewer than {self.config.min_findings} findings

## YOUR TASK
{self.config.task_prompt}

## OUTPUT REQUIREMENTS
- Write ALL findings to: {self.config.findings_file}
- Minimum {self.config.min_findings} findings required
- Use this format for each finding:

### Finding #N: [Title]
**Location:** file:line
**Severity:** Critical/High/Medium/Low
**Description:** What you found
**Recommendation:** How to fix

## WORKING DIRECTORY
{self.work_dir}

## INSTRUCTIONS
1. Start by listing all relevant files
2. Systematically review each file
3. Log findings IMMEDIATELY as you discover them
4. Continue until you have {self.config.min_findings}+ findings
5. If you finish early, go deeper - there's always more to find

The daemon is watching. Do thorough work.

BEGIN NOW.
"""

    def _build_resume_prompt(self, reason: str) -> str:
        """Build a resume prompt."""
        remaining = self.time_remaining()
        remaining_mins = int(remaining / 60)
        current_findings = self.get_findings_count()
        needed = self.config.min_findings - current_findings

        return f"""# SESSION RESUMED - DAEMON INSTRUCTION

You were resumed because: {reason}

## CURRENT STATUS
- Time remaining: {remaining_mins} minutes
- Current findings: {current_findings}
- Minimum required: {self.config.min_findings}
- Still need: {max(0, needed)} more findings

## YOUR TASK (CONTINUED)
{self.config.task_prompt}

## OUTPUT FILE
Continue writing to: {self.config.findings_file}

## INSTRUCTIONS
- Review what you've already found
- Go DEEPER into the codebase
- Find issues you missed the first time
- Check edge cases, error handling, security
- Every file has more to discover

The daemon is still watching. Continue working.

RESUME NOW.
"""


# ============================================================================
# Daemon Core
# ============================================================================

class AutonomousDaemon:
    """The main daemon that orchestrates autonomous work."""

    def __init__(self, config: DaemonConfig):
        self.config = config
        self.agents: Dict[str, ClaudeAgent] = {}
        self.running = True
        self.start_time = time.time()

        # Setup
        Path(config.log_dir).mkdir(parents=True, exist_ok=True)

        # Signal handling for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signals."""
        self.log("Shutdown signal received, stopping agents...")
        self.running = False

    def log(self, message: str):
        """Log a message with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")

        # Also write to daemon log
        log_file = Path(self.config.log_dir) / "daemon.log"
        with open(log_file, 'a') as f:
            f.write(f"[{timestamp}] {message}\n")

    def add_agent(self, agent_config: AgentConfig):
        """Add an agent to manage."""
        agent = ClaudeAgent(
            config=agent_config,
            work_dir=self.config.work_dir,
            log_dir=self.config.log_dir
        )
        self.agents[agent_config.name] = agent
        self.log(f"Added agent: {agent_config.name}")

    def launch_all(self):
        """Launch all agents."""
        for name, agent in self.agents.items():
            agent_id = agent.launch()
            self.log(f"Launched {name} (ID: {agent_id})")

    def check_agents(self):
        """Check status of all agents and resume if needed."""
        for name, agent in self.agents.items():
            # Skip if time is up for this agent
            if agent.time_remaining() <= 0:
                if agent.config.status != "completed":
                    agent.config.status = "completed"
                    agent.config.end_time = time.time()
                    self.log(f"✓ {name} completed (time expired)")
                continue

            # Check if agent stopped
            if not agent.is_running():
                findings = agent.get_findings_count()
                time_left = agent.time_remaining()
                time_left_mins = int(time_left / 60)

                # Determine resume reason
                if findings < agent.config.min_findings:
                    reason = f"Only {findings}/{agent.config.min_findings} findings. Need {agent.config.min_findings - findings} more."
                    self.log(f"↻ Resuming {name}: {reason}")
                    agent.resume(reason)
                elif time_left > 300:  # More than 5 min left
                    reason = f"Finished early with {time_left_mins} minutes remaining. Go deeper."
                    self.log(f"↻ Resuming {name}: {reason}")
                    agent.resume(reason)
                else:
                    agent.config.status = "completed"
                    agent.config.end_time = time.time()
                    self.log(f"✓ {name} completed ({findings} findings)")
            else:
                # Agent still running, just log status
                findings = agent.get_findings_count()
                time_left_mins = int(agent.time_remaining() / 60)
                if self.config.verbose:
                    self.log(f"  {name}: running ({findings} findings, {time_left_mins}m left)")

    def all_completed(self) -> bool:
        """Check if all agents are done."""
        return all(
            a.config.status == "completed"
            for a in self.agents.values()
        )

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all agents."""
        return {
            "total_agents": len(self.agents),
            "completed": sum(1 for a in self.agents.values() if a.config.status == "completed"),
            "total_findings": sum(a.get_findings_count() for a in self.agents.values()),
            "total_resumes": sum(a.config.resume_count for a in self.agents.values()),
            "agents": {
                name: {
                    "status": a.config.status,
                    "findings": a.get_findings_count(),
                    "resumes": a.config.resume_count,
                    "time_remaining_mins": int(a.time_remaining() / 60)
                }
                for name, a in self.agents.items()
            }
        }

    def run(self):
        """Main daemon loop."""
        self.log("=" * 60)
        self.log("AUTONOMOUS WORK DAEMON STARTED")
        self.log(f"Work directory: {self.config.work_dir}")
        self.log(f"Log directory: {self.config.log_dir}")
        self.log(f"Agents: {len(self.agents)}")
        self.log("=" * 60)

        # Launch all agents
        self.launch_all()

        # Main monitoring loop
        while self.running and not self.all_completed():
            time.sleep(self.config.check_interval)
            self.check_agents()

        # Final summary
        self.log("=" * 60)
        self.log("SESSION COMPLETE")
        summary = self.get_summary()
        self.log(f"Total findings: {summary['total_findings']}")
        self.log(f"Total resumes: {summary['total_resumes']}")
        for name, data in summary['agents'].items():
            self.log(f"  {name}: {data['findings']} findings, {data['resumes']} resumes")
        self.log("=" * 60)

        # Write summary to file
        summary_file = Path(self.config.log_dir) / "session_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)

        return summary


# ============================================================================
# CLI Interface
# ============================================================================

def load_implementation_prompt(work_dir: str, hours: float) -> str:
    """Load and populate the implementation god prompt."""
    prompt_file = Path(work_dir) / "scripts/autonomous/implementation_prompt.txt"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Implementation prompt not found: {prompt_file}")

    prompt = prompt_file.read_text()

    # Populate variables
    end_time = datetime.now() + timedelta(hours=hours)
    prompt = prompt.replace("{HOURS}", str(hours))
    prompt = prompt.replace("{MINUTES}", str(int(hours * 60)))
    prompt = prompt.replace("{END_TIME}", end_time.strftime("%Y-%m-%d %H:%M:%S"))
    prompt = prompt.replace("{MIN_CHANGES}", str(int(hours * 5)))  # 5 changes per hour
    prompt = prompt.replace("{OUTPUT_FILE}", f"{work_dir}/deep_work_logs/implementation_session.md")

    return prompt


def create_default_agents(work_dir: str, hours: float) -> List[AgentConfig]:
    """Create default agent configurations for Blackreach."""
    log_dir = f"{work_dir}/deep_work_logs"

    return [
        AgentConfig(
            name="performance",
            task_prompt="""Find every performance issue in this codebase.

Look for:
- Slow algorithms (O(n²) when O(n) is possible)
- Unnecessary loops and repeated computations
- Blocking I/O that could be async
- Memory leaks and excessive allocations
- Slow imports and startup time
- Missing caching opportunities
- Inefficient data structures

Examine every file in blackreach/ directory.""",
            findings_file=f"{log_dir}/performance_findings.md",
            min_findings=int(hours * 10),  # 10 findings per hour
            hours=hours
        ),
        AgentConfig(
            name="security",
            task_prompt="""Find every security vulnerability in this codebase.

Look for:
- Command injection in subprocess calls
- Path traversal in file operations
- SSRF in URL handling
- Credential exposure
- Weak cryptography
- SQL injection
- Information leaks in errors
- Race conditions

Examine every file in blackreach/ directory.""",
            findings_file=f"{log_dir}/security_findings.md",
            min_findings=int(hours * 7),  # 7 findings per hour
            hours=hours
        ),
        AgentConfig(
            name="code_quality",
            task_prompt="""Review every file for code quality issues.

Look for:
- Code smells (long functions, god classes)
- Naming issues
- Missing type hints
- Missing docstrings
- Dead code
- Duplication
- Inconsistent patterns
- Error handling gaps

Read EVERY file in blackreach/ directory.""",
            findings_file=f"{log_dir}/code_quality_findings.md",
            min_findings=int(hours * 15),  # 15 findings per hour
            hours=hours
        ),
        AgentConfig(
            name="test_gaps",
            task_prompt="""Find every gap in the test suite.

Look for:
- Untested functions
- Missing edge case tests
- Weak assertions
- Over-mocked tests
- Missing error path tests
- Flaky test patterns

Run coverage and analyze every file in tests/ directory.""",
            findings_file=f"{log_dir}/test_gap_findings.md",
            min_findings=int(hours * 10),  # 10 findings per hour
            hours=hours
        ),
        AgentConfig(
            name="implementation",
            task_prompt=load_implementation_prompt(work_dir, hours),
            findings_file=f"{log_dir}/implementation_session.md",
            min_findings=int(hours * 5),  # 5 substantial changes per hour
            hours=hours
        ),
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Claude Work Daemon - Force Claude to work for real time"
    )
    parser.add_argument("--hours", type=float, default=2.0,
                        help="Hours per agent (default: 2)")
    parser.add_argument("--work-dir", type=str,
                        default="/mnt/AI_Projects/Blackreach",
                        help="Working directory")
    parser.add_argument("--check-interval", type=int, default=60,
                        help="Seconds between status checks (default: 60)")
    parser.add_argument("--quiet", action="store_true",
                        help="Less verbose output")
    parser.add_argument("--agents", type=str, nargs="+",
                        default=["performance", "security", "code_quality", "test_gaps"],
                        help="Which agents to run")

    args = parser.parse_args()

    # Setup
    log_dir = f"{args.work_dir}/deep_work_logs"
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Create daemon config
    daemon_config = DaemonConfig(
        work_dir=args.work_dir,
        log_dir=log_dir,
        check_interval=args.check_interval,
        verbose=not args.quiet
    )

    # Create daemon
    daemon = AutonomousDaemon(daemon_config)

    # Add agents
    all_agents = {a.name: a for a in create_default_agents(args.work_dir, args.hours)}
    for agent_name in args.agents:
        if agent_name in all_agents:
            daemon.add_agent(all_agents[agent_name])
        else:
            print(f"Unknown agent: {agent_name}")
            print(f"Available: {list(all_agents.keys())}")
            return 1

    # Run
    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
