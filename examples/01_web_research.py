"""
Example 01: Web Research

Demonstrates how to use Blackreach to research a topic across the web
and return a summary. Shows how to set up callbacks to print live
progress as the agent works.

Run:
    python examples/01_web_research.py
"""

import os
import sys

# Add project root to path when running from the repo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackreach.agent import Agent, AgentCallbacks, AgentConfig
from blackreach.llm import LLMConfig


def detect_provider() -> LLMConfig:
    """
    Pick an LLM provider based on available environment variables.
    Falls back to Ollama (local) if no cloud key is set.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("[provider] Using Anthropic (claude-sonnet-4-6)")
        return LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key=os.environ["ANTHROPIC_API_KEY"],
        )
    if os.environ.get("OPENAI_API_KEY"):
        print("[provider] Using OpenAI (gpt-4o-mini)")
        return LLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key=os.environ["OPENAI_API_KEY"],
        )
    if os.environ.get("XAI_API_KEY"):
        print("[provider] Using xAI (grok)")
        return LLMConfig(
            provider="xai",
            model="grok-4-fast-non-reasoning",
            api_key=os.environ["XAI_API_KEY"],
        )
    print("[provider] Using Ollama (local) — ensure Ollama is running")
    return LLMConfig(provider="ollama", model="qwen2.5:7b")


def main():
    goal = (
        "Research the current state of quantum computing and summarize the "
        "3 most recent breakthroughs. Visit at least 2 sources."
    )

    llm_config = detect_provider()

    agent_config = AgentConfig(
        max_steps=20,
        headless=False,
    )

    step_count = [0]

    def on_step(step, max_steps, phase, detail):
        step_count[0] = step
        print(f"[step {step}/{max_steps}] {phase}: {detail}")

    def on_action(action, args):
        args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        print(f"  -> action: {action}({args_str})")

    def on_think(thought):
        # Print only the first 120 chars so output stays readable
        preview = thought[:120].replace("\n", " ")
        print(f"  .. thinking: {preview}...")

    def on_error(error):
        print(f"  [error] {error}")

    def on_complete(success, result):
        status = "SUCCESS" if success else "INCOMPLETE"
        print(f"\n[{status}] Agent finished after {step_count[0]} steps")

    callbacks = AgentCallbacks(
        on_step=on_step,
        on_action=on_action,
        on_think=on_think,
        on_error=on_error,
        on_complete=on_complete,
    )

    print(f"Goal: {goal}\n")
    agent = Agent(llm_config=llm_config, agent_config=agent_config, callbacks=callbacks)

    try:
        result = agent.run(goal)
        print("\n=== RESULT ===")
        print(result if result else "(No explicit result returned)")
    except KeyboardInterrupt:
        print("\n[interrupted] Run again with --resume to continue this session")


if __name__ == "__main__":
    main()
