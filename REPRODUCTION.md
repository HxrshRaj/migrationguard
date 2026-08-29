# Reproduction Guide

Tested on Python 3.11.15, Linux. No external services required for baseline mode or `--fake-llm`; advanced mode needs network access to `api.anthropic.com` and an API key.

## 0. Versions this was built and verified against

```
Python    3.11.15
pydantic  2.13.3
hypothesis 6.165.10
click     8.3.3
Jinja2    3.1.6
anthropic 1.2.0
pytest    9.1.1
```

Nothing else — no database server, no Docker, no network access required except for the real-Claude advanced run in step 4.

## 1. Clean environment setup

```bash
git clone <this repo's url> migrationguard
cd migrationguard
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Expected:** installs in well under a minute — the only third-party dependencies are pydantic, hypothesis, click, Jinja2, and anthropic (the last one is only *called* in advanced mode, but is always installed).

## 2. Run the test suite

```bash
pytest -q
```

**Expected output:**

```
................................                                       [100%]
32 passed in ~1-2s
```

If this doesn't pass, nothing downstream should be trusted — this is the step the whole project's credibility rests on. See `CHANGELOG.md` for what these tests were built to catch.

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

`out/baseline/report.html` should show: 7 findings, 5 auto-fixed, 125 test cases run, 92 identical, 0 cosmetic, 33 breaking. `out/baseline/trajectories.jsonl` should exist and be empty (baseline mode makes no LLM calls). **Runtime: well under a second.**

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

Same as 4a, but every `trajectories.jsonl` entry is a real disclosed Claude call (explanation generation for all 7 findings, fix generation for the 2 escalated ones, and a severity rationale for every non-identical case). **Runtime: roughly proportional to (7 explanation calls + up to 2 fix calls + one rationale call per non-identical case) × real API latency** — budget a few minutes for the full demo app, and check `trajectories.jsonl`'s `latency_ms` / `input_tokens` / `output_tokens` fields afterward for the exact cost and time actually spent.

`--seed` fixes Hypothesis's random source, so the *set of test inputs* generated is reproducible across runs even though the exact identical/breaking counts can shift slightly run to run if the model's fix wording changes.

## 5. What to look at

Open `out/<mode>/report.html` in a browser (no network needed to view it — everything is inlined). Look specifically at:

- The executive summary counts at the top.
- "What we're not confident about" — should list the 2 findings the baseline mode couldn't fix (both should disappear from this list in a successful advanced run).
- Any finding's "Smallest input that diverges" line — this is Hypothesis's shrunk minimal counterexample in advanced mode, or the first curated match in baseline mode.

`out/<mode>/run.jsonl` is the structured stage-by-stage log (one JSON object per line); `out/<mode>/trajectories.jsonl` is the agent-trajectory disclosure file.
