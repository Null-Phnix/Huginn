"""
Example 06: Custom Callbacks and Progress Tracking

Demonstrates the full Blackreach callback system. Shows how to hook into
every agent event to build a custom progress tracker, action log, and
live status display.

Available callbacks:
  on_step(step, max_steps, phase, detail)  - fired at each ReAct loop iteration
  on_action(action, args)                  - fired before each browser action
  on_observe(observation)                  - fired after each page observation
  on_think(thought)                        - fired after LLM reasoning
  on_error(error)                          - fired when a non-fatal error occurs
  on_complete(success, result)             - fired when the run finishes
  on_status(message)                       - fired for general status messages

Run:
    python examples/06_custom_callbacks.py
"""

import os
import sys
import time
from typing import Optional

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


class ProgressTracker:
    """
    A simple progress tracker built on top of Blackreach callbacks.

    Tracks timing, action counts, errors, and provides a summary at the end.
    """

    def __init__(self):
        self.start_time: Optional[float] = None
        self.action_counts: dict[str, int] = {}
        self.error_count: int = 0
        self.errors: list[str] = []
        self.steps_taken: int = 0
        self.thoughts: list[str] = []
        self.status_messages: list[str] = []

    def on_step(self, step: int, max_steps: int, phase: str, detail: str):
        if self.start_time is None:
            self.start_time = time.time()
        self.steps_taken = step
        elapsed = time.time() - (self.start_time or time.time())
        bar_filled = int((step / max_steps) * 20)
        bar = "#" * bar_filled + "-" * (20 - bar_filled)
        print(f"[{bar}] step {step:2d}/{max_steps} | {elapsed:5.1f}s | {phase}: {detail[:60]}")

    def on_action(self, action: str, args: Optional[dict]):
        self.action_counts[action] = self.action_counts.get(action, 0) + 1
        args_str = ""
        if args:
            # Show the most important arg only
            for key in ("url", "element", "text", "query"):
                if key in args:
                    val = str(args[key])[:50]
                    args_str = f" {key}={val!r}"
                    break
        print(f"    action: {action}{args_str}")

    def on_observe(self, observation: str):
        # Just count, don't print full observation
        pass

    def on_think(self, thought: str):
        # Store thoughts for summary
        self.thoughts.append(thought)
        # Print first line only
        first_line = thought.split("\n")[0][:100]
        print(f"    think: {first_line}")

    def on_error(self, error: str):
        self.error_count += 1
        self.errors.append(str(error)[:200])
        print(f"    ERROR: {error}")

    def on_status(self, message: str):
        self.status_messages.append(message)

    def on_complete(self, success: bool, result: Optional[dict]):
        elapsed = time.time() - (self.start_time or time.time())
        status = "SUCCESS" if success else "INCOMPLETE"
        print(f"\n{'='*60}")
        print(f"[{status}] Completed in {elapsed:.1f}s")
        self.print_summary()

    def print_summary(self):
        """Print a summary of what happened during the run."""
        print("\n--- Progress Summary ---")
        print(f"  Steps taken: {self.steps_taken}")
        elapsed = time.time() - (self.start_time or time.time())
        print(f"  Total time:  {elapsed:.1f}s")

        if self.action_counts:
            print("  Actions taken:")
            for action, count in sorted(self.action_counts.items(), key=lambda x: -x[1]):
                print(f"    {action}: {count}x")

        if self.error_count:
            print(f"  Errors: {self.error_count}")
            for err in self.errors[:3]:
                print(f"    - {err}")

        print(f"  LLM calls (thoughts): {len(self.thoughts)}")


def main():
    goal = (
        "Search for 'Python async await tutorial' on the web "
        "and return a summary of the best learning resources you find."
    )

    llm_config = detect_provider()
    agent_config = AgentConfig(
        max_steps=12,
        headless=False,
    )

    tracker = ProgressTracker()

    callbacks = AgentCallbacks(
        on_step=tracker.on_step,
        on_action=tracker.on_action,
        on_observe=tracker.on_observe,
        on_think=tracker.on_think,
        on_error=tracker.on_error,
        on_complete=tracker.on_complete,
        on_status=tracker.on_status,
    )

    print(f"Goal: {goal}")
    print("Tracking all agent events with ProgressTracker...\n")
    print("-" * 60)

    agent = Agent(llm_config=llm_config, agent_config=agent_config, callbacks=callbacks)

    try:
        result = agent.run(goal, quiet=True)  # quiet=True — callbacks handle all output
        print("\n=== FINAL RESULT ===")
        if isinstance(result, dict):
            final = result.get("result", result.get("summary", ""))
            print(final if final else str(result))
        else:
            print(str(result))

    except KeyboardInterrupt:
        print("\n[interrupted]")
        tracker.print_summary()


if __name__ == "__main__":
    main()
