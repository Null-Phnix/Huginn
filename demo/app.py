"""
Huginn Demo App — Streamlit interface for web scraping and crawling.
Run: streamlit run demo/app.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx
import streamlit as st

API_BASE = "http://localhost:7432"

st.set_page_config(
    page_title="Huginn Demo",
    page_icon="🕷️",
    layout="wide",
)


# ─── API Helpers ──────────────────────────────────────────────────────

def get_client(api_key: str) -> httpx.Client:
    return httpx.Client(base_url=API_BASE, headers={"Authorization": f"Bearer {api_key}"})


def check_health(api_key: str) -> dict:
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def scrape_url(api_key: str, url: str, formats: list, webhook_url: str = "") -> dict:
    client = get_client(api_key)
    payload = {"url": url, "formats": formats}
    if webhook_url:
        payload["webhook_url"] = webhook_url
    r = client.post("/v1/probe", json=payload)
    r.raise_for_status()
    return r.json()


def crawl_url(api_key: str, url: str, max_depth: int, limit: int, webhook_url: str = "") -> dict:
    client = get_client(api_key)
    payload = {"url": url, "maxDepth": max_depth, "limit": limit}
    if webhook_url:
        payload["webhook_url"] = webhook_url
    r = client.post("/v1/sweep", json=payload)
    r.raise_for_status()
    return r.json()


def get_jobs(api_key: str) -> list:
    client = get_client(api_key)
    r = client.get("/v1/jobs")
    r.raise_for_status()
    return r.json().get("jobs", [])


# ─── Sidebar ─────────────────────────────────────────────────────────

st.sidebar.title("🕷️ Huginn")
st.sidebar.markdown("**v1.1** — Self-hosted web scraping")

with st.sidebar:
    st.subheader("Connection")
    api_key = st.text_input("API Key", value=st.session_state.get("api_key", ""), type="password")
    st.session_state["api_key"] = api_key

    if st.button("Check Health"):
        if api_key:
            health = check_health(api_key)
            st.json(health)
        else:
            st.warning("Enter an API key first")

    st.divider()
    st.markdown("[📡 OpenAPI Docs](http://localhost:7432/docs)")
    st.markdown("[🐳 Docker Setup](../Dockerfile)")


# ─── Main Tabs ───────────────────────────────────────────────────────

tab_scrape, tab_crawl, tab_scheduled, tab_jobs = st.tabs(
    ["Scrape", "Crawl", "Scheduled Jobs", "Job History"]
)

# ─── Scrape Tab ──────────────────────────────────────────────────────

with tab_scrape:
    st.header("Scrape a URL")
    col1, col2 = st.columns([3, 1])

    with col1:
        url = st.text_input("URL", placeholder="https://example.com")
    with col2:
        formats = st.multiselect(
            "Formats",
            ["markdown", "html", "links", "screenshot"],
            default=["markdown"],
        )

    webhook_url = st.text_input("Webhook URL (optional)", placeholder="https://your-endpoint.com/callback")

    if st.button("Scrape", type="primary", use_container_width=True):
        if not api_key:
            st.error("Enter API key in sidebar")
        elif not url:
            st.error("Enter a URL")
        elif not formats:
            st.error("Select at least one format")
        else:
            with st.spinner("Scraping..."):
                try:
                    start = time.time()
                    result = scrape_url(api_key, url, formats, webhook_url)
                    elapsed = time.time() - start

                    st.success(f"Done in {elapsed:.2f}s")

                    for fmt in formats:
                        if fmt == "markdown" and "markdown" in result.get("data", {}):
                            md = result["data"]["markdown"]
                            st.markdown("**Markdown**")
                            st.markdown(md[:2000] + ("..." if len(md) > 2000 else ""), unsafe_allow_html=False)
                        elif fmt == "html" and "html" in result.get("data", {}):
                            st.markdown("**HTML**")
                            st.code(result["data"]["html"][:2000], language="html")
                        elif fmt == "links" and "links" in result.get("data", {}):
                            links = result["data"]["links"]
                            st.markdown(f"**Links ({len(links)} found)**")
                            for link in links[:50]:
                                st.markdown(f"- {link}")
                        elif fmt == "screenshot" and "screenshot" in result.get("data", {}):
                            import base64
                            img_data = result["data"]["screenshot"]
                            if img_data:
                                st.image(base64.b64decode(img_data), width=600)

                    with st.expander("Full JSON"):
                        st.json(result)

                except httpx.HTTPStatusError as e:
                    st.error(f"HTTP {e.response.status_code}: {e.response.text}")
                except Exception as e:
                    st.error(str(e))

# ─── Crawl Tab ───────────────────────────────────────────────────────

with tab_crawl:
    st.header("Crawl a Site")
    col1, col2 = st.columns([3, 1])

    with col1:
        crawl_url_input = st.text_input("URL", placeholder="https://example.com", key="crawl_url")
    with col2:
        max_depth = st.number_input("Max Depth", min_value=1, max_value=10, value=2)
        limit = st.number_input("Page Limit", min_value=1, max_value=1000, value=50)

    webhook_url_crawl = st.text_input("Webhook URL (optional)", placeholder="https://your-endpoint.com/callback", key="crawl_webhook")

    if st.button("Crawl", type="primary", use_container_width=True):
        if not api_key:
            st.error("Enter API key in sidebar")
        elif not crawl_url_input:
            st.error("Enter a URL")
        else:
            with st.spinner("Crawling..."):
                try:
                    start = time.time()
                    result = crawl_url(api_key, crawl_url_input, max_depth, limit, webhook_url_crawl)
                    elapsed = time.time() - start

                    st.success(f"Done in {elapsed:.2f}s")

                    pages = result.get("data", {}).get("pages", [])
                    st.markdown(f"**Crawled {len(pages)} pages**")

                    for page in pages[:20]:
                        with st.container():
                            cols = st.columns([4, 1, 1])
                            with cols[0]:
                                st.markdown(f"**{page.get('url', 'unknown')}**")
                            with cols[1]:
                                status = page.get("status", "unknown")
                                st.write(f"Status: {status}")
                            with cols[2]:
                                st.write(f"Depth: {page.get('depth', '?')}")
                            if page.get("markdown"):
                                st.markdown(page["markdown"][:300] + "...")

                    if len(pages) > 20:
                        st.info(f"Showing 20 of {len(pages)} pages")

                    with st.expander("Full JSON"):
                        st.json(result)

                except httpx.HTTPStatusError as e:
                    st.error(f"HTTP {e.response.status_code}: {e.response.text}")
                except Exception as e:
                    st.error(str(e))

# ─── Scheduled Jobs Tab ───────────────────────────────────────────────

with tab_scheduled:
    st.header("Scheduled Jobs")

    job_types = {
        "scrape": {"label": "Scrape (single URL)", "fields": ["url", "formats"]},
        "crawl": {"label": "Crawl (site)", "fields": ["url", "maxDepth", "limit"]},
        "map": {"label": "Map (URL discovery)", "fields": ["url"]},
        "distill": {"label": "Distill (LLM extract)", "fields": ["url", "prompt"]},
    }

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Create Schedule")
        sched_name = st.text_input("Schedule Name", placeholder="daily-docs-check")
        sched_type = st.selectbox("Job Type", list(job_types.keys()), format_func=lambda x: job_types[x]["label"])
        sched_cron = st.text_input("Cron Expression", value="0 9 * * *", help="Standard cron: minute hour day month weekday")
        sched_webhook = st.text_input("Webhook URL", placeholder="https://your-endpoint.com/callback")

        st.markdown("**Job Request**")
        if sched_type == "scrape":
            req_url = st.text_input("URL")
            req_formats = st.multiselect("Formats", ["markdown", "html", "links"], default=["markdown"])
            request_json = {"url": req_url, "formats": req_formats}
        elif sched_type == "crawl":
            req_url = st.text_input("URL")
            req_depth = st.number_input("Max Depth", value=2)
            req_limit = st.number_input("Page Limit", value=50)
            request_json = {"url": req_url, "maxDepth": req_depth, "limit": req_limit}
        elif sched_type == "map":
            req_url = st.text_input("URL")
            request_json = {"url": req_url}
        else:
            req_url = st.text_input("URL")
            req_prompt = st.text_area("LLM Prompt")
            request_json = {"url": req_url, "prompt": req_prompt}

        if st.button("Create Schedule", type="primary"):
            if not api_key:
                st.error("Enter API key")
            elif not sched_name:
                st.error("Enter a schedule name")
            else:
                client = get_client(api_key)
                payload = {
                    "name": sched_name,
                    "job_type": sched_type,
                    "cron": sched_cron,
                    "request": request_json,
                }
                if sched_webhook:
                    payload["webhook_url"] = sched_webhook
                try:
                    r = client.post("/v1/schedule", json=payload)
                    r.raise_for_status()
                    st.success(f"Schedule created: {r.json()}")
                except Exception as e:
                    st.error(str(e))

    with col2:
        st.subheader("Active Schedules")
        if api_key:
            if st.button("Refresh"):
                st.rerun()
            try:
                client = get_client(api_key)
                r = client.get("/v1/schedule")
                r.raise_for_status()
                schedules = r.json().get("schedules", [])
                if schedules:
                    for s in schedules:
                        with st.container():
                            st.markdown(f"**{s.get('name', 'unnamed')}** `{s.get('job_type', '')}`")
                            st.caption(f"Cron: {s.get('cron', '?')} | Status: {s.get('status', '?')}")
                            cols = st.columns([1, 1, 1])
                            if cols[0].button("Pause", key=f"pause_{s['id']}"):
                                try:
                                    client.post(f"/v1/schedule/{s['id']}/pause")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                            if cols[1].button("Resume", key=f"resume_{s['id']}"):
                                try:
                                    client.post(f"/v1/schedule/{s['id']}/resume")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                            if cols[2].button("Delete", key=f"del_{s['id']}"):
                                try:
                                    client.delete(f"/v1/schedule/{s['id']}")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                            st.divider()
                else:
                    st.info("No schedules yet")
            except Exception as e:
                st.error(str(e))
        else:
            st.info("Enter API key to see schedules")

# ─── Job History Tab ─────────────────────────────────────────────────

with tab_jobs:
    st.header("Job History")

    if api_key:
        if st.button("Refresh Jobs"):
            st.rerun()

        try:
            jobs = get_jobs(api_key)
            if jobs:
                for job in jobs[:50]:
                    status = job.get("status", "unknown")
                    status_emoji = {"running": "🔄", "completed": "✅", "failed": "❌", "pending": "⏳"}.get(status, "❓")
                    with st.expander(f"{status_emoji} {job.get('id', '?')} — {job.get('type', job.get('job_type', 'unknown'))}"):
                        st.json(job)
            else:
                st.info("No jobs yet")
        except Exception as e:
            st.error(str(e))
    else:
        st.info("Enter API key to see job history")

