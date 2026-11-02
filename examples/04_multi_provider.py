"""
Example 04: Multi-Provider Configuration

Shows how to configure Blackreach with different LLM providers and
run the same task with whichever provider is available. Demonstrates
the provider detection pattern and how to construct LLMConfig for each.

Run:
    python examples/04_multi_provider.py

    # Or specify a provider explicitly:
    ANTHROPIC_API_KEY=sk-ant-... python examples/04_multi_provider.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blackreach.agent import Agent, AgentCallbacks, AgentConfig
from blackreach.llm import LLMConfig

# Provider configurations — edit models to your preference
PROVIDER_CONFIGS = {
    "anthropic": LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        temperature=0.3,
    ),
    "openai": LLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        temperature=0.3,
    ),
    "xai": LLMConfig(
        provider="xai",
        model="grok-4-fast-non-reasoning",
        api_key=os.environ.get("XAI_API_KEY", ""),
        temperature=0.3,
    ),
    "google": LLMConfig(
        provider="google",
        model="gemini-2.5-flash",
        api_key=os.environ.get("GOOGLE_API_KEY", ""),
        temperature=0.3,
    ),
    "ollama": LLMConfig(
        provider="ollama",
        model="qwen2.5:7b",
        temperature=0.3,
    ),
}


def select_provider() -> tuple[str, LLMConfig]:
    """
    Select the first available provider based on environment variables.
    Falls back to Ollama (no API key required).
    """
    priority_order = ["anthropic", "openai", "xai", "google"]

    for name in priority_order:
        config = PROVIDER_CONFIGS[name]
        if config.api_key:
            return name, config

    return "ollama", PROVIDER_CONFIGS["ollama"]


def list_configured_providers() -> list[str]:
    """Return a list of providers that have API keys configured."""
    configured = []
    for name, config in PROVIDER_CONFIGS.items():
        if name == "ollama" or config.api_key:
            configured.append(name)
    return configured


def main():
    goal = "Search for 'what is a transformer neural network' and return a one-paragraph summary."

    configured = list_configured_providers()
    print("Configured providers:", ", ".join(configured))

    provider_name, llm_config = select_provider()
    print(f"Selected provider: {provider_name} (model: {llm_config.model})")
    print(f"Goal: {goal}\n")

    agent_config = AgentConfig(
        max_steps=8,
        headless=False,
    )

    def on_step(step, max_steps, phase, detail):
        print(f"[step {step}/{max_steps}] {phase}: {detail}")

    def on_action(action, args):
        args_str = ", ".join(f"{k}={v!r}" for k, v in (args or {}).items())
        print(f"  -> {action}({args_str})")

    def on_complete(success, result):
        print(f"\n[{'SUCCESS' if success else 'INCOMPLETE'}]")

    callbacks = AgentCallbacks(
        on_step=on_step,
        on_action=on_action,
        on_complete=on_complete,
    )

    agent = Agent(llm_config=llm_config, agent_config=agent_config, callbacks=callbacks)

    try:
        result = agent.run(goal)
        print("\n=== RESULT ===")
        print(result if result else "(No explicit result)")
    except KeyboardInterrupt:
        print("\n[interrupted]")


if __name__ == "__main__":
    main()
