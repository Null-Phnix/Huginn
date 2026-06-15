# Huginn Improvement Plan — 2026-06-15 Nüwa pass

> Sandbox: `/home/phnix/.nuwa/sandboxes/huginn-20260615_015705/`
> Source: github.com/Null-Phnix/Huginn @ main (v1.2.0)
> Baseline: **343 tests pass** (311 outer + 32 inner) when both test dirs are collected; 6 network tests deselected.

---

## Goal

Targeted improvements only. No rewrites. No feature work. Fix clear bugs, fix obvious inconsistencies, ensure tests catch what they claim to. Everything else stays put.

---

## Findings (ordered by severity) — RE-VERIFIED

| # | Finding | Evidence | Severity |
|---|---------|----------|----------|
| 1 | FastAPI app `version="1.1.0"` ×3 (create_app + 2 health endpoints) is stale; package is `1.2.0` | `huginn/api.py:193, 249, 265` | **bug** |
| 2 | `huginn/sdk.py` `HuginnClient.base_url` default = `:8000`; rest of codebase is `:7432` (cli.py, config.py, docker-compose, huginn_cli.py, sdk/python) | `huginn/sdk.py:5, 74, 88, 688` | **bug — broken out-of-box for SDK users** |
| 3 | `huginn/sdk.py` `HuginnSync` example in docstring uses `:7432`; class default is `:8000` — example contradicts class | `huginn/sdk.py:676 vs 688` | **bug — example lies** |
| 4 | `tests/test_sdk.py` asserts default `base_url == "http://localhost:8000"` — locks the wrong port in place | `tests/test_sdk.py:13, 16, 17` | **bug — test is wrong, not the code** |
| 5 | README quick-start uses port 8000 in 3 curl examples + `huginn serve --port 8000` + `HUGINN_PORT=8000` env example — all should be 7432 | `README.md:111,117,123,198,288` | **bug — users follow broken examples** |
| 6 | `docs/UPGRADE_PLAN.md:100` shows `docker run -p 8000:8000` and `HUGINN_API_KEY=*** | `docs/UPGRADE_PLAN.md:100` | **bug** |
| 7 | Test badge `343_passing` is misleading: `pytest` only runs 311 because `pyproject.toml` `testpaths = ["tests"]` excludes `huginn/tests/` (32 tests) | pyproject vs collected | **bug — CI/devs see 311 not 343** |
| 8 | `BrowserConfig.user_agent` default: `Huginn/1.1 (+https://huginn.dev/bot)` — version doesn't match `__version__ = "1.2.0"` | `huginn/config.py:25` | **bug** |
| 9 | `.env.example` UA default: `Huginn/1.1 (+https://huginn.dev/bot)` and comment says `Huginn/1.1` | `.env.example:39, 40` | **bug** |

## False positives (re-verified, NOT bugs)

- ~~`ErrorCode.UNAUTHORIZED="***"`~~ — actually `"unauthorized"`. Was grep terminal-truncation artifact (long lines truncated to `***` in the display).
- ~~`.env.example` `HUGINN_API_KEY=***` line smash~~ — the `***` is a deliberate placeholder for the user to fill in. The line itself is on its own line.

---

## Plan (ordered, small commits)

Each task = one commit. Tests run between tasks. Nothing in `pyproject.toml` "all" extras or anything that could affect downstream.

### Task 1: Centralize version string (api.py + health endpoints)
- Add `from huginn import __version__` to `huginn/api.py` at top.
- Replace `version="1.1.0"` in `create_app` with `version=__version__`.
- Replace hard-coded `"1.1.0"` in `health` and `health_detailed` endpoints with `__version__`.
- Add a test that asserts the FastAPI app's `version` attr matches `huginn.__version__` (so it can never drift again).
- Run full suite.

### Task 2: Fix SDK default port (8000 → 7432)
- Update `tests/test_sdk.py` so the default-base-url tests expect `http://localhost:7432` (TDD: tests first).
- Run tests → see them fail (RED).
- Patch `huginn/sdk.py:88` (HuginnClient default) and `huginn/sdk.py:688` (HuginnSync default).
- Update `huginn/sdk.py:5, 74, 676` to match.
- Run tests → green.

### Task 3: Fix README port (8000 → 7432)
- Update `README.md:111, 117, 123, 198, 288` from 8000 to 7432.
- Update `docs/UPGRADE_PLAN.md:100`.
- Verify nothing imports these strings (search codebase to be safe).

### Task 4: Include `huginn/tests/` in default pytest collection
- Update `pyproject.toml` `testpaths` to `["tests", "huginn/tests"]`.
- Verify `pytest` (no args) now reports 343 pass.
- Update README badge text "343 passing" — keep the number, but make sure the run command actually produces it.
- Add a sanity test (or simple `pytest --collect-only` step) that asserts both directories are discovered.

### Task 5: Fix `User-Agent` default version
- In `huginn/config.py`, make `BrowserConfig.user_agent` default reference `__version__` (e.g. `f"Huginn/{__version__} (+https://huginn.dev/bot)"`).
- Update `.env.example` comment `Huginn/1.1` → `Huginn/1.2` and the `HUGINN_USER_AGENT` default value.
- Add test that asserts the default UA contains the current `__version__`.
- Run full suite.

### Task 6: Final sweep
- Run full suite (both test dirs) — must be 343+ pass.
- `ruff check` (if installed) — fix anything it flags.
- Commit message: "chore: code review pass — version/port/UA/test discovery consistency".

---

## Tests added in this pass (so far)

- `tests/test_models.py::TestErrorCode` — 3 tests verifying UNAUTHORIZED value, all values match a lowercase-identifier pattern, HTTP 401 mapping. These tests are **new** but the production code already passed them; they're protection against future drift.


---

## Out of scope (noted, not done)

- Consolidating the two SDKs (large, design discussion needed)
- Documenting Firecrawl-compat route aliases (docs-only)
- FastAPI global state refactor (large, risky)
- `await` patterns in api.py endpoints (working as-is, no bugs found)
- Any new features

---

## Verification (post-apply)

```bash
# 1. Both test dirs run, all pass
.venv-test/bin/pytest tests huginn/tests -q
# Expected: 343 passed, 6 deselected in <10s

# 2. New tests added in this pass still pass
.venv-test/bin/pytest tests/test_models.py tests/test_sdk.py tests/test_api.py -v

# 3. Version consistency
.venv-test/bin/python -c "from huginn import __version__; print(__version__)"
# Expected: 1.2.0

# 4. Port consistency in SDK
.venv-test/bin/python -c "from huginn.sdk import HuginnClient; print(HuginnClient().base_url)"
# Expected: http://localhost:7432

# 5. ErrorCode.UNAUTHORIZED has real value
.venv-test/bin/python -c "from huginn.models import ErrorCode; print(repr(ErrorCode.UNAUTHORIZED.value))"
# Expected: 'unauthorized'
```

---

## Deliverables reminder (per user prompt)

- [ ] GitHub + vault recon summary → in vault project log
- [ ] This plan → `.nuwa/plans/2026-06-15_huginn-improvement-pass.md` ✓
- [ ] Exact repo changes → captured in commit history
- [ ] Test results → captured in vault log
- [ ] Project log path → `02-Projects/Active/BlackCrawl.md` updated OR new sibling file in `02-Projects/Active/Huginn.md`
- [ ] Remaining issues + next steps → in vault log
