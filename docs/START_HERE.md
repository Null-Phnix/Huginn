# Start Here — Project Blackreach

## What Is This?

Ghost Hand evolved. A general-purpose autonomous browser agent that:
- Takes any goal
- Figures out where to go
- Downloads/extracts what you need
- Uses local models (no restrictions)

## Prerequisites

```bash
# 1. Install a capable local model
ollama pull qwen2.5:14b-instruct

# 2. Test it works
ollama run qwen2.5:14b-instruct "Say hello"

# 3. Python deps (when ready to build)
pip install playwright aiosqlite
playwright install chromium
```

## To Start Building

Open Claude Code in this folder:
```bash
cd /mnt/AI_Projects/Blackreach
claude
```

Then say:
> "Read ARCHITECTURE.md. Let's build Blackreach Phase 1 — the core ReAct loop. Start with the basic agent that can observe a page, think about what to do, and take an action."

## What You Can Steal from Ghost Hand

Location: `/mnt/AI_Projects/The Library of Alexandria/The Ghost Hand/`

Reusable:
- `ghost_hand/puppet.py` — Playwright browser setup
- `ghost_hand/loop.py` — Agent loop structure (refactor needed)
- Download handling logic

Don't copy:
- Site-specific strategies
- Mythology prompts
- Hardcoded paths

## The Goal

```bash
blackreach "find and download the top 10 papers on mixture of experts from 2024"
```

And it just... does it.

## Development Order

1. **ReAct loop** — observe/think/act cycle working
2. **Local LLM** — talking to Ollama
3. **Actions** — click, type, download, etc.
4. **Memory** — don't repeat yourself
5. **Planner** — break complex goals into steps
6. **Recovery** — handle failures gracefully
7. **CLI** — nice interface

Start with #1. Get one loop working. Everything else builds on that.
