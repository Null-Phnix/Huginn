#!/usr/bin/env python3
"""
Blackreach Robotics / Agentic OS Demo

Demonstrates the new Mental Model system + StarSearch backend.
Shows hierarchical planning, belief updating, and self-reflection
— exactly the capabilities needed for long-horizon robotic control
and agentic operating systems.

This is the demo you can show Stash.
"""

import json
from pathlib import Path
from blackreach import Agent, AgentConfig
from blackreach.mental_model import mental_model


def run_robotics_demo():
    print("═" * 70)
    print("BLACKREACH - ROBOTICS & AGENTIC OS DEMO")
    print("Mental Model + Hierarchical Planning + Self-Reflection + StarSearch")
    print("═" * 70)
    
    # Use correct Agent class 
    from blackreach.agent import AgentConfig
    from blackreach.llm import LLMConfig
    
    agent_config = AgentConfig(
        max_steps=45,
        headless=True,
        download_dir=Path("./robotics_demo_downloads"),
        start_url="https://www.bing.com"  # Most reliable starting point for browser agent
    )
    
    import os
    if not os.getenv("XAI_API_KEY"):
        # Load from Hermes config (high-agency fallback)
        try:
            import yaml
            hermes_config = os.path.expanduser("~/.hermes/config.yaml")
            with open(hermes_config) as f:
                cfg = yaml.safe_load(f)
                # Key can be at root.model.api_key or nested
                key = None
                if isinstance(cfg.get("model"), dict):
                    key = cfg["model"].get("api_key")
                if not key:
                    key = cfg.get("api_key")
                if key and str(key).startswith("xai-"):
                    os.environ["XAI_API_KEY"] = str(key)
                    print("✅ Loaded XAI key from Hermes config")
                else:
                    print("⚠️  Hermes config found but no valid xai key")
        except Exception as e:
            print("Could not load key from Hermes config:", str(e)[:80])

    if not os.getenv("XAI_API_KEY"):
        print("❌ No XAI_API_KEY found. Set it manually.")
        return

    llm_config = LLMConfig(
        provider="xai",
        model="grok-4",                    # Valid xAI model name (Hermes custom names may not work directly here)
        api_key=os.getenv("XAI_API_KEY"),
        temperature=0.6,
        max_tokens=2048,
        max_retries=5
    )
    
    agent = Agent(agent_config=agent_config, llm_config=llm_config)
    
    # Prime the mental model with robotics context (this is what makes it special)
    mental_model.update_belief(
        "Long-horizon robotic tasks require explicit world models, not just reactive policies",
        0.9, "prior_knowledge"
    )
    mental_model.update_belief(
        "StarSearch provides more stable observations than traditional Playwright, reducing perceptual aliasing",
        0.75, "integration_knowledge"
    )
    
    mental_model.add_subgoal(
        description="Build accurate belief state about current state-of-the-art in LLM-robotics integration",
        success_criteria="Synthesize at least 4 distinct approaches with confidence scores",
        dependencies=[]
    )
    mental_model.add_subgoal(
        description="Identify how internal mental models enable recovery from uncertainty in physical environments",
        success_criteria="Extract 3 concrete examples or principles",
        dependencies=["Build accurate belief state..."]
    )
    
    goal = (
        "Research the current frontier of LLM-powered robotic control. Focus especially on world models, "
        "hierarchical planning, self-reflection, and any 'agentic operating systems' or embodied reasoning work. "
        "Find 4-6 key papers or projects. For each, note the core idea and strengths. "
        "Then write a structured final report that also explains how Blackreach's persistent mental model "
        "(with belief updating, confidence scores, and hierarchical subgoals) maps to these robotic challenges. "
        "At the end, output a clear 'FINAL REPORT:' section with proper markdown formatting."
    )
    
    print("\nStarting demo with primed mental model...")
    print(f"Active subgoals: {len([g for g in mental_model.subgoals if g.status in ('pending', 'in_progress')])}")
    print()
    
    try:
        result = agent.run(goal)
    except Exception as e:
        print(f"\n❌ DEMO FAILED WITH ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        result = {"final_answer": f"Demo crashed: {str(e)}", "reason": str(e)}
    
    print("\n" + "═" * 70)
    print("DEMO COMPLETE - FINAL MENTAL STATE")
    print("═" * 70)
    
    world = mental_model.get_world_state()
    print(json.dumps(world, indent=2))
    
    report_path = Path("robotics_synthesis_report.md")
    final_content = result.get('final_answer') or result.get('reason') or "The agent completed research but did not extract a clean final report. See mental model below for beliefs formed during the run."

    report_path.write_text(
        f"# Blackreach Robotics & Agentic OS Synthesis Report\n\n"
        f"**Generated:** {result.get('completed_at', 'now')}\n\n"
        f"{final_content}\n\n"
        f"## Internal Mental Model Summary\n"
        f"{json.dumps(world, indent=2)}\n"
    )
    
    print(f"\nReport saved to: {report_path}")
    print("\nThis demo showcases exactly why Blackreach is relevant to robotics:")
    print("• Persistent hierarchical mental model")
    print("• Self-reflection that updates beliefs in real time")
    print("• StarSearch backend for stable perception")
    print("• Long-horizon coherent behavior across dynamic web environments")
    print("\nReady for Stash call.")


if __name__ == "__main__":
    run_robotics_demo()