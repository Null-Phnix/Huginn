# Huginn — Personal Firecrawl Replacement (Spec)

> Starting reference point. Re-read before any work in this thread.
>
> **Author:** Nüwa (M3)
> **Date:** 2026-06-15
> **For:** Josii
> **Status:** active direction

---

## Goal (one line)

Huginn = personal Firecrawl replacement. Self-hosted, on your box, with your name on it. 1:1 endpoint + config parity. Then expand scope.

## Non-goals

- Not a SaaS. No multi-tenant. No billing.
- Not chasing Firecrawl on cloud browser fleet or residential proxies.
- Not killing the project if it doesn't ship.

## Three layers (in order)

### Layer 1 — Branding (small, one config)

Every user-facing string lives in one file: `huginn/_branding.py`. Everything else reads from it.

```python
name = "Huginn"
short_name = "huginn"
binary = "huginn"
package = "huginn"
ua = f"Huginn/{__version__} (+https://huginn.dev/bot)"
color = "purple"
ascii_banner = "..."
```

If you ever want to rename, change `name` + `binary` + `package` + `ascii_banner`. Done.

### Layer 2 — Endpoint parity (the actual work)

Audit Firecrawl's openapi spec and diff against Huginn. Ship one parity feature per commit. 5/week = full 1:1 in 3 weeks.

Initial gap table (snapshot 2026-06-15):

| Firecrawl feature | Huginn status | Action |
|---|---|---|
| `/scrape` `summary` field | missing | add |
| `/scrape` `changeTracking` diff | missing | add |
| `/scrape` `mobile: true` | missing | add |
| `/scrape` `skipTlsVerification` | missing | add |
| `/scrape` `blockAds`, `removeBase64Images` | missing | add |
| `/scrape` `includeTags`/`excludeTags` | exists | verify all 5+ tags work |
| `/crawl` `maxConcurrency` per-job | global | make per-job |
| `/extract` `webhook` signed | unsigned | add HMAC |
| `/search` returns crawlable URLs | partial | wire up |
| `/batch/scrape` `ignoreInvalidURLs` | missing | add |
| `/map` `sitemap: "include\|skip\|only"` | missing | add |
| metadata.language detection | missing | add |
| iframe content extraction | missing | add |
| pdf output format | exists | write real test |
| LLM extraction quality (default model, eval set) | ad-hoc | pick default + 50-page test |

### Layer 3 — Reliability (the hard part, post-1:1)

The "never think about Firecrawl again" stuff. Each is real work:

1. **Replay log** — every scrape records: dom snapshot hash, what worked, what failed, network log, content hash. One sqlite table. Debug instead of just "5xx."
2. **Per-domain adaptive throttling** — start slow, ramp up on success, back off on failure.
3. **Selector memory** — when DOM changes, log diff, try new selector first next time.
4. **Better `huginn doctor`** — actually catch the things that bite (playwright, chromium, data dir, stealth patches, UA not blocked by CF basic list).

## What I won't do

- Two SDKs (kill `sdk/python/` or merge into `huginn/sdk.py`)
- Rewrite the global mutable state in api.py (working as-is)
- Rewrite the crawler
- Chase Firecrawl on hosted infra

## Success criteria

- `huginn serve` starts on your box
- `curl localhost:7432/v1/probe` works
- Every Firecrawl call you make today has a Huginn equivalent
- Zero monthly spend
- Layer 3 features at your pace

## Process (from prior thread)

- One feature per commit. Small, reviewable.
- TDD: failing test → green test → refactor.
- Run full suite after every commit. Baseline: 350 pass.
- Re-read this spec before any session work in this thread.

## Open questions for you

- Default LLM model for extraction? (You use Claude Opus 4.8 for Nabu. Worth using the same here for consistency?)
- Repo URL — push `nuwa/huginn-2026-06-15-pass` to `Null-Phnix/Huginn` as a PR? Or keep it local for now?
- Vault log destination — update existing `02-Projects/Active/BlackCrawl.md` (which is also the Huginn note) or create separate `02-Projects/Active/Huginn.md`?
