"""
Example 05: Session Save and Resume

Demonstrates Blackreach's session persistence feature. The agent runs
a multi-step goal, saves its state, and shows how to resume from exactly
where it left off.

This is useful for:
- Long research tasks that might be interrupted
- Workflows that span multiple terminal sessions
- Debugging by inspecting state between steps

Run:
    # First run (will pause partway through and save state)
    python examples/05_session_resume.py --save

    # Resume from saved state (use the session ID printed by --save)
    python examples/05_session_resume.py --resume 42
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackreach.agent import Agent, AgentCallbacks, AgentConfig
from blackreach.llm import LLMConfig


def detect_provider() -> LLMConfig:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key=os.environ["ANTHROPIC_API_KEY"],
        )
    if os.environ.get("OPENAI_API_KEY"):
        return LLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key=os.environ["OPENAI_API_KEY"],
        )
    print("[provider] Defaulting to Ollama — ensure Ollama is running locally")
    return LLMConfig(provider="ollama", model="qwen2.5:7b")


def run_with_save(agent: Agent, goal: str):
    """
    Run a goal and demonstrate session save.
    After a few steps, manually trigger a pause to show the save mechanism.
    In practice, Ctrl+C triggers this automatically.
    """
    step_counter = [0]
    pause_at_step = 3  # Pause after this many steps to demonstrate save

    def on_step(step, max_steps, phase, detail):
        step_counter[0] = step
        print(f"[step {step}/{max_steps}] {phase}: {detail}")
        # Demonstrate pause after a few steps
        if step >= pause_at_step:
            print(f"\n[demo] Pausing at step {step} to demonstrate session save...")
            agent.pause()

    def on_complete(success, result):
        paused = agent._paused
        if paused:
            print(f"\n[paused] Session #{agent.session_id} saved.")
            print(f"  Resume with: python examples/05_session_resume.py --resume {agent.session_id}")
        else:
            print(f"\n[{'SUCCESS' if success else 'INCOMPLETE'}] Session #{agent.session_id}")

    callbacks = AgentCallbacks(
        on_step=on_step,
        on_complete=on_complete,
    )
    agent.callbacks = callbacks

    print(f"Goal: {goal}")
    print(f"Will auto-pause after {pause_at_step} steps to demonstrate save...\n")
    result = agent.run(goal)
    return result


def run_resumed(agent: Agent, session_id: int):
    """Resume a previously saved session."""

    def on_step(step, max_steps, phase, detail):
        print(f"[step {step}/{max_steps}] {phase}: {detail}")

    def on_action(action, args):
        args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        print(f"  -> {action}({args_str})")

    def on_complete(success, result):
        print(f"\n[{'SUCCESS' if success else 'INCOMPLETE'}] Resumed session finished")

    callbacks = AgentCallbacks(
        on_step=on_step,
        on_action=on_action,
        on_complete=on_complete,
    )
    agent.callbacks = callbacks

    print(f"Resuming session #{session_id}...\n")
    result = agent.resume(session_id)
    return result


def main():
    parser = argparse.ArgumentParser(description="Blackreach session resume demo")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--save", action="store_true", help="Run and save session state")
    group.add_argument("--resume", type=int, metavar="SESSION_ID", help="Resume a saved session")
    args = parser.parse_args()

    goal = (
        "Search for recent news about open source AI models released in 2025. "
        "Visit at least 3 sources and compile a list of the top 5 models."
    )

    llm_config = detect_provider()
    agent_config = AgentConfig(
        max_steps=20,
        headless=False,
    )

    agent = Agent(llm_config=llm_config, agent_config=agent_config)

    try:
        if args.resume:
            result = run_resumed(agent, args.resume)
        else:
            # Default to --save behavior if no flag given
            result = run_with_save(agent, goal)

        print("\n=== RESULT SUMMARY ===")
        if isinstance(result, dict):
            print(f"  Success: {result.get('success', 'unknown')}")
            print(f"  Session ID: {result.get('session_id', 'unknown')}")
            print(f"  Steps taken: {result.get('steps_taken', 0)}")
            downloads = result.get("downloads", [])
            if downloads:
                print(f"  Downloads: {len(downloads)} file(s)")
            paused = result.get("paused", False)
            if paused:
                print(f"\n  To resume: python examples/05_session_resume.py --resume {result.get('session_id')}")

    except KeyboardInterrupt:
        print("\n[interrupted] Session state has been auto-saved.")
        if agent.session_id:
            print(f"  Resume with: python examples/05_session_resume.py --resume {agent.session_id}")


if __name__ == "__main__":
    main()
