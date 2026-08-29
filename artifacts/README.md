# Disclosure artifacts

`out/` is git-ignored (it's regenerated on every run), so the artifacts from
the one **real** advanced-mode run are copied here to ship with the submission.

Run: `migrationguard scan --mode advanced --provider latentstack --max-examples 150 --seed 20260830`
Date: 2026-08-29 · Gateway: LatentStack (`https://latentstack.dev/v1`) · Model: `gemini/gemini-3.1-pro`

| File | What it is |
|---|---|
| `trajectories-latentstack.jsonl` | **The agent-trajectory disclosure.** 39 lines, one per LLM call (7 `scanner.explain` + 2 `fixgen.rewrite` + 30 `diffengine.rationale`). Each line: prompt, response, model, input/output token counts, latency. Every entry is `"model": "gemini/gemini-3.1-pro"`. Produced automatically by `migrationguard/llm.py`, never hand-assembled. |
| `report-advanced-latentstack.html` | The rendered verification report for that run (self-contained, opens offline). 7/7 fixed, 683 cases → 473 identical / 0 cosmetic / 210 breaking. |
| `run-latentstack.jsonl` | The structured stage-by-stage log for that run. |

Headline numbers and how to reproduce: `REPRODUCTION.md` §4b. Full write-up: `CHANGELOG.md` entry 19.
