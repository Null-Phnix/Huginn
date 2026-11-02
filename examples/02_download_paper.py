"""
Example 02: Download an Academic Paper

Demonstrates how to configure Blackreach to find and download a file.
The agent navigates to arXiv, finds the "Attention Is All You Need" paper,
and downloads the PDF.

After the run, the script prints any files found in the download directory.

Run:
    python examples/02_download_paper.py
"""

import os
import sys
from pathlib import Path

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
        "Find the original 'Attention Is All You Need' transformer paper on arXiv "
        "(arxiv.org/abs/1706.03762) and download the PDF."
    )

    download_dir = Path("./downloads/papers")
    download_dir.mkdir(parents=True, exist_ok=True)

    llm_config = detect_provider()

    agent_config = AgentConfig(
        max_steps=15,
        headless=False,
        download_dir=download_dir,
    )

    downloaded_files = []

    def on_step(step, max_steps, phase, detail):
        print(f"[step {step}/{max_steps}] {phase}: {detail}")

    def on_action(action, args):
        if action == "download":
            url = (args or {}).get("url", "")
            print(f"  -> downloading: {url}")

    def on_download(filename, path, size_bytes):
        downloaded_files.append(path)
        size_kb = size_bytes / 1024
        print(f"  [download] Saved: {filename} ({size_kb:.1f} KB)")

    def on_complete(success, result):
        print(f"\n[{'SUCCESS' if success else 'INCOMPLETE'}] Agent finished")

    callbacks = AgentCallbacks(
        on_step=on_step,
        on_action=on_action,
        on_complete=on_complete,
    )
    # on_download is not part of AgentCallbacks dataclass but shown for documentation

    print(f"Goal: {goal}")
    print(f"Download directory: {download_dir.resolve()}\n")

    agent = Agent(llm_config=llm_config, agent_config=agent_config, callbacks=callbacks)

    try:
        result = agent.run(goal)
        print("\n=== RESULT ===")
        print(result if result else "(No explicit result)")
    except KeyboardInterrupt:
        print("\n[interrupted]")

    # Print what was downloaded
    print("\n=== DOWNLOADED FILES ===")
    files = list(download_dir.glob("**/*"))
    files = [f for f in files if f.is_file()]
    if files:
        for f in files:
            size_kb = f.stat().st_size / 1024
            print(f"  {f.name} ({size_kb:.1f} KB)")
    else:
        print("  (no files found — the agent may not have completed the download)")


if __name__ == "__main__":
    main()
