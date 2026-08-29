# MigrationGuard

**MigrationGuard doesn't migrate your code — it proves whether the migration you already have is safe to ship.**

Built solo for LatentForce.ai's BuildSprint (Aug 28–30, 2026).

## Who this is for

An engineer who just ran an AI coding tool over a legacy codebase — LatentCode or anything else — to modernize it. The migration finished. It compiles. It looks clean. It passes the tests someone thought to write.

## The bottleneck

"Looks right" and "behaves the same" are different claims, and the gap between them is where production incidents live. A migrated function can handle 95% of inputs perfectly and silently diverge on the 5% nobody wrote a test for — a name with an apostrophe, an empty string, a value that used to crash and now doesn't (or the reverse). That kind of bug doesn't show up in code review. It shows up months later, in production, and by then it's expensive to trace back to "the migration."

"Migration complete ✅" is not evidence. It's a claim. MigrationGuard's job is to replace that claim with proof, or with an honest account of exactly where the proof runs out.

## What it does

**Stage 1 — Risk scanning.** Walks a codebase for a specific class of risky pattern, flags every instance, explains in plain language why it's risky, and proposes a fix with a confidence score.

**Stage 2 — Behavioral verification.** For every proposed fix, generates a battery of test inputs — including edge cases a human reviewer is unlikely to think of — and runs the original and the fixed code side by side against every one of them. Any divergence is flagged and classified by severity. This is the part that actually earns the word "proof": not "the new code looks parameterized," but "here are hundreds of cases where it behaves identically, and here is exactly the input where it doesn't, shrunk down to the smallest example that still reproduces it."

## This submission's scope

Building a general-purpose migration verifier in 46 hours isn't credible, so this submission targets one narrow, real migration class end to end rather than many classes shallowly: **unsafe, string-formatted SQL query construction → parameterized queries** (f-strings, `%`-formatting, `str.format()`, `string.Template`, and string concatenation, all landing in a `sqlite3` `execute()`/`executemany()` call). It's demonstrated against a small bundled "legacy" app (`migrationguard/demo/legacy_app.py`) so the whole pipeline — scan, fix, verify, report — runs against real, if intentionally small, code rather than a synthetic example built to make the demo look good.

The architecture doesn't assume this is the only class MigrationGuard will ever check. `fixgen/` is an interface — anything that produces a `FixCandidate` can be verified — deliberately, so the verifier stays useful regardless of what generated the fix: LatentCode, a different tool, or a human. That's also the honest answer to "isn't this just a smaller LatentCode": MigrationGuard doesn't compete with a migration tool, it sits downstream of one and checks its work.

## Built with LatentCode

LatentCode (LatentForce.ai's coding agent, gateway `latentstack.dev`) shows up in this build in two concrete ways, both visible in git history:

1. **Three merged pull requests, authored by the LatentCode agent.** Each was a scoped task — [#1](https://github.com/HxrshRaj/migrationguard/pull/1) added `string.Template` as a fifth detected SQL pattern, [#2](https://github.com/HxrshRaj/migrationguard/pull/2) added `--json` / `--fail-on` flags for CI use, [#3](https://github.com/HxrshRaj/migrationguard/pull/3) wired LatentCode itself in as a second LLM provider — that the agent implemented and opened as a PR; each was then reviewed (real bugs found and fixed — a leaked config key, a `UnicodeEncodeError`, dead code) before merge. `CHANGELOG.md` entries 16–19 document each one, including the review fixups.
2. **Advanced mode runs on LatentCode's inference gateway.** `migrationguard scan --mode advanced --provider latentstack` routes every model call (explanation, fix generation, severity rationale) through `latentstack.dev` to `gemini/gemini-3.1-pro`. The one real end-to-end run is fully disclosed at `artifacts/trajectories-latentstack.jsonl` (39 calls) — 7/7 findings auto-fixed, 683 verification cases, ~$0.31, ~7 min. See `REPRODUCTION.md` §4b.

## A note on what "divergence" means here

Because the migration class in this demo is a *security* fix, not a pure refactor, some divergence between the original and fixed code is the point, not a bug: the original code is supposed to behave differently on an input like `O'Brien` or `'; DROP TABLE users; --` once the fix is applied. MigrationGuard's severity verdict (`identical` / `cosmetic` / `breaking`) is a fact about whether behavior changed — it deliberately does not try to guess whether that change was intended. The report adds a separate, clearly-labeled interpretation on top of every non-identical case (a heuristic in baseline mode — does the input contain a SQL metacharacter? — and an LLM-written explanation in advanced mode) to help a reviewer tell "the fix correctly closing a hole" apart from "an actual regression on ordinary input." See `CHANGELOG.md` for why keeping this out of the verdict itself, rather than folding it in, was a deliberate design choice.

## Quickstart

```bash
pip install -e ".[dev]"
migrationguard scan --mode baseline
migrationguard scan --mode advanced --fake-llm   # no API key needed, exercises the full pipeline
migrationguard scan --mode advanced              # real Claude calls; needs ANTHROPIC_API_KEY
migrationguard scan --mode advanced --provider latentstack # real LatentStack calls; needs LATENTSTACK_API_KEY
migrationguard scan --path path/to/your/code     # scan a file or directory instead of the bundled demo
open out/baseline/report.html                    # or out/advanced/report.html
```

`--path` defaults to the bundled demo app, so every command above and
every number in `REPRODUCTION.md` is unchanged when it's omitted. Pointed
at your own code, the scanner and fix generator run over every `.py` file;
behavioral verification still only runs for the demo (it needs a seeded
database with a matching schema to exercise the code — see "What we're not
confident about").

See `REPRODUCTION.md` for exact commands, expected output, and versions.

## Baseline vs. advanced

| Stage | Baseline | Advanced |
|---|---|---|
| Scanner | AST pattern match, canned explanation text | + LLM writes a context-specific explanation and confidence score |
| Fix generator | Deterministic AST rewrite — fixes what it can prove is safe to rewrite mechanically (5 of 7 findings in the demo) | + LLM rewrite for what the template can't handle (the remaining 2 of 7) — reads the whole function, not one expression |
| Test generation | ~27 hand-picked adversarial strings, applied per parameter | Hypothesis-generated, type-aware, hundreds of cases, auto-shrunk to a minimal failing example |
| Severity interpretation | Rule-based heuristic (does the input look adversarial?) | LLM-written rationale per divergence |
| Verdict itself (`identical`/`cosmetic`/`breaking`) | **Always deterministic, in both modes** — see "A note on what divergence means" above | Same |

## Architecture

```
demo/  → scanner/  → fixgen/  → verifier/ (testgen → harness → diffengine) → report/
```

Five modules, each independently testable:

- **`scanner/`** — `ast`-based detection of risky query construction. `queryexpr.py` is the shared AST analysis both the scanner and the baseline fixer depend on, so their classifications never disagree with each other about what's fixable.
- **`fixgen/`** — `baseline.py` (deterministic rewrite) and `advanced.py` (LLM rewrite with a self-check on the parameter list), behind one interface.
- **`verifier/`** — the part that most needs to be trustworthy, so it's the most heavily tested. `harness.py` runs original vs. fixed against fresh, isolated in-memory SQLite databases; `diffengine.py` is pure and deterministic (see the note above on why); `testgen.py` builds the input battery for both modes.
- **`report/`** — renders a single self-contained HTML file, no CDN dependency, so it opens correctly offline.
- **`orchestrator/`** — the CLI (`--path` for scanning arbitrary files/directories, `--mode`, `--fake-llm`, `--max-examples`, `--seed`), and the only place that writes `run.jsonl` (structured logs) and `trajectories.jsonl` (every LLM call, disclosed). Prints an estimated LLM cost for real advanced-mode runs, derived from the token counts in `trajectories.jsonl` (`cost.py`).

## What we're not confident about

- The severity model has three buckets and no fourth "expected due to security fix" bucket at the verdict level — see the note above.
- The advanced fixer's self-check confirms the parameter *list* is unchanged; it doesn't verify parameter *order semantics* beyond that (a rewrite that swapped two same-typed parameters would pass the self-check and only get caught by the verifier finding the resulting divergence — which it does, but that's a second line of defense, not prevention).
- `search_users_by_email_domain`'s LLM-generated fix moves the `%` wildcard into the bound parameter (`LIKE ?` with `f"%{domain}"`); this is the standard, correct pattern, but it does mean the fixed function's exact query string differs from a hand-written parameterization someone might have expected.
- Advanced-mode numbers in `REPRODUCTION.md` §4b were captured on 2026-08-29 against the **LatentStack gateway** (`--provider latentstack`, `gemini/gemini-3.1-pro`): 7/7 findings auto-fixed, 683 verification cases (473 identical / 0 cosmetic / 210 breaking), 39 disclosed LLM calls with 0 failures, ~7 min, ~$0.31 (placeholder rate — token counts are exact). The disclosed trajectory log ships at `artifacts/trajectories-latentstack.jsonl`. `--fake-llm` reproduces the pipeline shape with no key; `--provider anthropic` (the default) runs it on Claude instead (`ANTHROPIC_API_KEY`).
- The `gemini/gemini-3.1-pro` price in `cost.py` is a placeholder pending LatentStack's published gateway pricing, so the ~$0.31 cost figure is an estimate; the 8,292-in / 29,894-out token counts behind it are exact (from `trajectories.jsonl`).
- A real Claude call that fails every retry (rate limit, transient 5xx, dropped connection) is *degraded, not fatal*: that finding falls back to its canned explanation / not-auto-fixed status / heuristic rationale, the failed call is written to `trajectories.jsonl` as `<CALL FAILED ...>`, and the report's "What we're not confident about" lists it. An auth/permission error still stops the run — nothing downstream would work.
- `str.format()` (CHANGELOG entry 9) and `string.Template` (entry 16) detection and template-fixing are each covered by unit tests but are not exercised by the bundled demo app, which is deliberately frozen at 7 findings so its documented numbers stay exact. `string.Template` was drafted by the LatentStack coding agent and reviewed before merge — see entry 16.
- `--path` generalizes *scanning and fix generation* to any file or directory, but *behavioral verification* still only runs for the bundled demo: the harness seeds an in-memory SQLite database from a fixed fixture (`demo/fixtures.py`), so it can only exercise functions that expect that schema. Verifying arbitrary code would need a per-project fixtures provider — a real design direction, out of scope for this submission. Findings outside the demo are scanned, fix-generated, and reported, with the missing verification called out explicitly in the report.
- The LLM cost figure is an estimate: exact recorded token counts × a per-model rate hand-entered in `cost.py` from Anthropic's public pricing (checked 2026-08-29). If the published rates change, that constant is what goes stale — the token counts in `trajectories.jsonl` stay exact.

## Agent trajectories

Every LLM call this project makes goes through one client (`migrationguard/llm.py`) and is logged to `trajectories.jsonl` on every advanced-mode run — prompt, response, model, token counts, latency. That file is the disclosed agent-trajectory artifact for this submission; it's produced automatically, never hand-assembled after the fact.

## Development

```bash
pip install -e ".[dev]"
pytest -q          # test suite
ruff check .       # lint
mypy               # type-check
```

`.github/workflows/ci.yml` runs all three on push and PR, against Python 3.11 and 3.12. `ruff` and `mypy` are both clean; the config (`pyproject.toml`) is deliberately pragmatic rather than maximally strict — see the comments there.

## License

MIT — see [`LICENSE`](LICENSE). (Copyright line reads "MigrationGuard authors"; set your own name there before publishing.)
