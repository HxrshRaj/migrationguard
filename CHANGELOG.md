# Improvement Changelog

Entries are in build order. Each is tied to evidence produced by actually running the system, not to a plan.

## 1. Baseline scanner + fixgen: AST pattern matching + template rewrite

Detector walks every `execute()`/`executemany()` call via `ast`, classifies the query-building expression (f-string / `%`-format / concatenation / untraceable), and the baseline fixer mechanically rewrites what it can prove is safe to rewrite.

**Evidence:** against the demo app's 7 findings, the baseline fixer succeeds on 5 and correctly declines the other 2 with a stated reason each (`out/baseline/report.html`, "What we're not confident about" section). Zero false positives on the two already-safe negative-control functions (`tests/test_scanner.py::test_no_false_positives_on_already_safe_functions`).

## 2. Bug found and fixed: quoted placeholders produced invalid SQL

First version of the query-expression analyzer replaced each interpolated value with `?` in place, without touching the surrounding literal text. For a pattern like `f"WHERE name = '{name}'"`, that produced `"WHERE name = '?'"` — a `?` **inside** single quotes, which sqlite3 parses as the two-character string literal `?`, not a bind placeholder. Every fixed function using this shape would have silently returned zero rows for every input.

**Evidence:** caught during manual smoke-testing before any test existed for it — `generate_fix()`'s output on `find_user_by_name` printed `"...name = '?'"` verbatim. Fixed by rewriting the analyzer to build (literal, param) segments and only strip a `'` when it sits on **both** sides of a placeholder; when a quote sits on only one side (discovered on `search_users_by_email_domain`'s `LIKE '%' + domain` pattern — a wildcard baked into the SQL text), the analyzer now correctly reports the shape as recognized-but-unfixable-by-template rather than guessing. `tests/test_queryexpr.py::test_fstring_with_quoted_placeholder_strips_quotes` and `::test_concat_with_asymmetric_quote_is_recognized_but_unfixable` pin both behaviors down.

## 3. Verifier core wired end to end: harness + diff engine + baseline curated tests

**Evidence:** first full baseline run: 125 test cases across the 5 fixable findings, 92 identical, 0 cosmetic, 33 breaking — every breaking case traced (by input) to a SQL metacharacter, matching the intended "the fix changes behavior on adversarial input, not ordinary input" story. `search_users_by_email_domain` and `list_active_users_over_age` correctly produced no verification result at all, since no fix exists yet to verify against.

## 4. Verifier's own test suite

32 tests: diff-engine classification (table-driven, every case category), DB isolation between calls, a seeded-bug catch on an ordinary benign input (not just adversarial ones, to rule out confusing "intended security divergence" with "real regression"), and a no-false-positive control comparing two behaviorally identical implementations written two different ways.

**Evidence:** `pytest -q` → 32 passed, ~1.5s.

## 5. Advanced (Hypothesis) test generation wired in

## 6. Bug found and fixed: `from __future__ import annotations` silently broke type-aware generation

`testgen.param_types()` originally read `inspect.Parameter.annotation` directly and checked `annotation is int`. The demo module uses `from __future__ import annotations` (PEP 563), which turns every annotation into an unevaluated string at runtime — so `annotation` was the string `"int"`, and `annotation is int` was `False` for every single parameter, including genuinely `int`-typed ones. Every parameter silently fell back to the adversarial-string strategy.

**Evidence:** before the fix, advanced-mode verification of `list_active_users_over_age(conn, min_age: int, role: str)` reported **71 out of 71 cases as BREAKING** — including cases that manual testing confirmed behave identically (e.g. `(20, "admin")`). A 100% divergence rate on a correct fix was the tell. Fixed by switching to `typing.get_type_hints()`, which resolves PEP 563 string annotations back to real types via the function's own `__globals__`. After the fix, the same function reports a mix of identical and breaking cases (roughly half and half, consistent with `role` being the parameter that actually carries risk) — matching manual spot-checks exactly.

This is entry 6 rather than a footnote on entry 5 deliberately: it's a more serious bug than #2 — it didn't crash or error, it silently produced a plausible-looking but wrong report. Nothing about the pipeline's control flow signaled failure; only the report's own numbers, read skeptically, did.

## 7. Advanced scanner explanation + LLM fixer + trajectory disclosure

`fixgen/advanced.py` escalates only the findings the baseline template declines (2 of 7 in the demo), rather than re-running every finding through the model — cheaper and more deterministic where a mechanical rewrite is already provably correct, while still reaching 7/7 fixed overall. Every LLM call, real or fake, is logged to `trajectories.jsonl` by one shared client wrapper (`migrationguard/llm.py`), so disclosure can't be accidentally incomplete.

**Evidence:** `migrationguard scan --mode advanced --fake-llm` completes end to end with a deterministic stand-in client (no API key required) — 7/7 findings fixed, 540 total test cases across all seven, `trajectories.jsonl` populated and tagged `model="fake-llm-client"` so a dry run is never mistaken for real Claude output.

## 8. Report generator

Single self-contained HTML file, no CDN dependency. Executive summary, per-finding cards, a "what we're not confident about" section driven by fix success and confidence thresholds, and — per finding — the smallest input Hypothesis found that diverges, plus a heuristic (baseline) or LLM-written (advanced) interpretation of whether each divergence looks like the intended fix or a possible regression.

---

## Main failure mode

The severity model has exactly three verdict buckets (`identical` / `cosmetic` / `breaking`) and, deliberately, no fourth bucket for "breaking because the fix is *supposed* to change this input's behavior." That interpretation lives one layer up, in the report (a heuristic in baseline mode, an LLM sentence in advanced mode) — never inside the trusted verdict itself. On this demo's migration class that's the right call: folding "is this expected" into the ground-truth verdict would mean trusting a heuristic or a model to decide what counts as a bug, which is exactly the kind of unverifiable confidence this project exists to replace. But it does mean the report currently asks a reviewer to read two things together (the verdict, and its interpretation) rather than one, and on a migration class where divergence is never intentional, that second layer would be pure overhead rather than the load-bearing distinction it is here.

## Hot take

The more interesting failure mode this build surfaced wasn't in the *migration* being verified — it was in the verifier itself, twice (entries #2 and #6). Both bugs looked fine on a casual read of the diff. Both only surfaced because something downstream refused to accept a suspicious-looking result at face value: a test suite in one case, a report number that was too clean to be true (100% divergence) in the other. That's the actual argument for a tool like this one, applied recursively: "looks right, passes the obvious check" wasn't good enough for the AI-migrated code MigrationGuard exists to verify, and it wasn't good enough for MigrationGuard's own code either. A migration-verification tool that hasn't had its own logic held to the same standard it demands of the code it checks doesn't get to claim the standard is achievable.
