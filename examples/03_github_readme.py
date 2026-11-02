"""
Example 03: Fetch README from a GitHub Repository

A simple, fast demo. The agent navigates to a GitHub repository page
and returns the installation instructions from the README.

This example works reliably since GitHub is a well-known static/hybrid site
that Blackreach has tuned timeout parameters for.

Run:
    python examples/03_github_readme.py
"""

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


def main():
    goal = (
        "Go to github.com/anthropics/anthropic-sdk-python and fetch the README content. "
        "Return the installation instructions."
    )

    llm_config = detect_provider()

    agent_config = AgentConfig(
        max_steps=10,
        headless=False,
        start_url="https://github.com/anthropics/anthropic-sdk-python",
    )

    def on_step(step, max_steps, phase, detail):
        print(f"[step {step}/{max_steps}] {phase}: {detail}")

    def on_action(action, args):
        args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        print(f"  -> {action}({args_str})")

    def on_complete(success, result):
        status = "SUCCESS" if success else "INCOMPLETE"
        print(f"\n[{status}]")

    callbacks = AgentCallbacks(
        on_step=on_step,
        on_action=on_action,
        on_complete=on_complete,
    )

    print(f"Goal: {goal}\n")
    agent = Agent(llm_config=llm_config, agent_config=agent_config, callbacks=callbacks)

    try:
        result = agent.run(goal)
        print("\n=== RESULT ===")
        print(result if result else "(No explicit result returned)")
    except KeyboardInterrupt:
        print("\n[interrupted]")


if __name__ == "__main__":
    main()
