# Reproduction Guide

Requires Python ≥ 3.11. Verified on 3.11 (Linux and Windows 11) and 3.12 (CI). No external services required for baseline mode or `--fake-llm`; advanced mode needs network access to `api.anthropic.com` and an API key.

## 0. Versions this pass was verified against

The exact resolved versions on the clean-environment pass (Python 3.11.9, Windows). `pip install -e ".[dev]"` resolves the latest release inside each `pyproject.toml` range, so yours may differ slightly — the pinned ranges, not these exact patch versions, are the contract.

```
Python     3.11.9
pydantic   2.13.5
hypothesis 6.165.10
click      8.5.0
Jinja2     3.1.6
anthropic  1.2.0
pytest     8.4.2
ruff       0.15.x     (dev)
mypy       (latest)   (dev)
```

Nothing else — no database server, no Docker, no network access required except for the real-Claude advanced run in step 4b.

## 1. Clean environment setup

```bash
git clone <this repo's url> migrationguard
cd migrationguard
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

**Expected:** installs in well under a minute — the third-party dependencies are pydantic, hypothesis, click, Jinja2, and anthropic (the last one is only *called* in advanced mode, but is always installed), plus ruff and mypy for the dev extra.

> **Windows note:** the `anthropic` package ships some very deeply-nested file paths. If `pip install` fails with `OSError: [Errno 2] No such file or directory: ...beta_managed_agents_...py`, enable Long Path support (`git config --system core.longpaths true` is not enough — set the `LongPathsEnabled` registry key / group policy) or clone to a short path such as `C:\src\migrationguard`. Linux/macOS are unaffected.

## 2. Run the test suite

```bash
pytest -q
```

**Expected output:**

```
........................................................................ [100%]
79 passed
```

Runtime is a few seconds on Linux; on a cold Windows checkout it's ~1–2 minutes (process-spawn overhead plus Hypothesis's shrink phase — not a problem, just slower). If any test fails, nothing downstream should be trusted — this is the step the whole project's credibility rests on. See `CHANGELOG.md` for what these tests were built to catch (the original 32, plus the post-submission additions in entries 9–16).

## 3. Baseline run (no API key, no network)

```bash
migrationguard scan --mode baseline --out-dir out/baseline
```

**Expected output** (counts are deterministic — no LLM, no randomness):

```
Scanned migrationguard/demo/legacy_app.py: 7 finding(s), 5 auto-fixed.
Report:      out/baseline/report.html
Run log:     out/baseline/run.jsonl
Trajectories:out/baseline/trajectories.jsonl
```

`out/baseline/report.html` should show: 7 findings, 5 auto-fixed, 167 test cases run, 129 identical, 0 cosmetic, 38 breaking. (These went up from 125 / 92 / 33 when the curated adversarial-input list grew from 20 strings to 27 — see `CHANGELOG.md` entry 13. Still fully deterministic.) `out/baseline/trajectories.jsonl` should exist and be empty (baseline mode makes no LLM calls). **Runtime: well under a second.**

## 4a. Advanced run — dry run, no API key

```bash
migrationguard scan --mode advanced --fake-llm --out-dir out/advanced-dryrun --max-examples 150
```

Exercises the full pipeline shape — including the LLM-escalation path for the 2 findings the baseline template can't fix — with a deterministic canned client instead of a real model call. Useful for confirming the wiring works before spending API budget, and for CI.

**Expected:** 7 findings, **7/7 auto-fixed** (the 2 escalated ones now succeed via the fake LLM), `out/advanced-dryrun/trajectories.jsonl` populated with entries tagged `"model": "fake-llm-client"`. **Runtime: a few seconds** (Hypothesis's shrink phase dominates; not model latency).

## 4b. Advanced run — real Claude calls

```bash
export ANTHROPIC_API_KEY=sk-ant-...
migrationguard scan --mode advanced --out-dir out/advanced --max-examples 150 --seed 20260830
```

Same as 4a, but every `trajectories.jsonl` entry is a real disclosed Claude call (explanation generation for all 7 findings, fix generation for the 2 escalated ones, and a severity rationale for every non-identical case). **Runtime: roughly proportional to (7 explanation calls + up to 2 fix calls + one rationale call per non-identical case) × real API latency** — budget a few minutes for the full demo app.

The run's final line prints an **estimated LLM cost** — the exact input/output token counts recorded in `trajectories.jsonl`, multiplied by the published per-million-token rate for `claude-sonnet-4-5-20250929` ($3 in / $15 out, hand-entered in `migrationguard/cost.py`). It looks like:

```
LLM cost:    ~$0.0000 over N priced call(s) (XXXX in + YYYY out tokens)
```

Baseline mode and `--fake-llm` make no billable calls, so this line does not appear for them — their output is unchanged from sections 3 and 4a.

> **This submission's advanced-mode numbers have not yet been captured against a real key.** Every advanced-mode figure quoted in this repo currently comes from the `--fake-llm` dry run in 4a. To get real numbers: `export ANTHROPIC_API_KEY=sk-ant-...`, run the 4b command above, then read the `LLM cost:` line and `trajectories.jsonl` (`latency_ms`, `input_tokens`, `output_tokens`) for the true cost and wall-clock time. Ship that `trajectories.jsonl` as the agent-trajectory disclosure artifact.

`--seed` fixes Hypothesis's random source, so the *set of test inputs* generated is reproducible across runs even though the exact identical/breaking counts can shift slightly run to run if the model's fix wording changes.

## 4c. Scanning code other than the bundled demo

```bash
migrationguard scan --mode baseline --path path/to/your/code --out-dir out/mycode
```

`--path` takes a file or a directory (walked recursively for `.py` files; `__pycache__`, `.venv`, `build`, etc. are skipped). Findings and proposed fixes from every file are aggregated into one `report.html`. Behavioral verification only runs for the bundled demo app — for any other file the report shows the finding and the proposed fix with the missing verification stated explicitly in "What we're not confident about". A scan that finds nothing still writes a valid (empty) report.

## 5. What to look at

Open `out/<mode>/report.html` in a browser (no network needed to view it — everything is inlined). Look specifically at:

- The executive summary counts at the top.
- "What we're not confident about" — should list the 2 findings the baseline mode couldn't fix (both should disappear from this list in a successful advanced run).
- Any finding's "Smallest input that diverges" line — this is Hypothesis's shrunk minimal counterexample in advanced mode, or the first curated match in baseline mode.

`out/<mode>/run.jsonl` is the structured stage-by-stage log (one JSON object per line); `out/<mode>/trajectories.jsonl` is the agent-trajectory disclosure file.
