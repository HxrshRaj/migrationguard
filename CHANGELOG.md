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

## Improvement pass (post-submission hardening, Aug 29 2026)

Entries below were made in a second pass over the original submission. The
goal was to close the widest "toy demo vs. real tool" gaps without
destabilising the verifier core or the reproduction numbers above.

## 9. `str.format()` added as a fourth detected pattern

The scanner scoped four string-formatting shapes for this migration class
(f-string, `%`-format, concatenation, `str.format()`) but only shipped
three; `"... {} ...".format(x)` was recognised as risky only accidentally,
via the `UNTRACEABLE` catch-all, with no dedicated classification and no
template fix. `queryexpr._analyze_format()` now parses the format template
with `string.Formatter().parse()`, maps every field (`{}`, `{0}`, `{name}`)
back to its `.format()` argument in *placeholder* order, and feeds the
result through the same `(literal, param)` segment machinery the other
three patterns use — so the quote-stripping fix from entry #2 and the
asymmetric-quote refusal both apply to `str.format()` for free.

**Evidence:** 14 new tests. `tests/test_queryexpr.py` pins down 12 shapes:
auto-numbered, manual-numbered (including out-of-order fields, where the
bind order must follow the SQL text and not the arg list —
`test_format_method_manual_numbering_binds_in_template_order`), keyword
fields, and five distinct *recognised-but-not-mechanically-fixable* cases
(format spec `{0:d}`, conversion `{!r}`, mixed auto/manual numbering,
`LIKE '%{}'` asymmetric quoting, arg-count mismatch) that come back
classified as `format_method` with `parameterized_sql=None` rather than
silently mis-rewritten. `tests/test_scanner.py` and
`tests/test_fixgen_baseline.py` confirm the pattern is wired end to end
(canned explanation + confidence, no `KeyError`) and that a two-field
`str.format()` query is rewritten to `WHERE name = ? AND role = ?` with
the quotes stripped and no `'?'` left behind.

This capability is covered by unit tests rather than by an 8th function in
`demo/legacy_app.py`: keeping the bundled demo at exactly 7 findings keeps
every number in `README.md` and `REPRODUCTION.md` (and entries #1–#8
above) accurate as-written. The tradeoff is that `--mode advanced` on the
demo app doesn't exercise `str.format()` end to end; the analyzer and the
fixer are exercised directly instead.

## 10. `--path`: scan any file or directory, not just the bundled demo

The CLI hard-coded `migrationguard/demo/legacy_app.py`. It now takes
`--path` (a file or a directory walked recursively for `.py` files, with
`__pycache__` / `.venv` / `build` / etc. skipped). Findings, fixes, and
verifications from every file aggregate into one `RunReport` and one
`report.html`. Finding ids are disambiguated by file path when more than
one file is scanned, so two files with a `lookup()` at the same line
don't collide. Unreadable and non-Python-3 files are logged and skipped
rather than aborting the run.

Behavioral verification still only runs for the bundled demo. This is a
real limitation, not an oversight: `verifier/harness.py` seeds an
in-memory SQLite database from `demo/fixtures.py`, so it can only
*exercise* functions that expect that exact schema. For findings outside
the demo, the report shows the finding and the proposed fix and states
plainly, in "What we're not confident about", that it was not verified
("N finding(s) outside the bundled demo app were scanned and
fix-generated but not behaviourally verified"). Verifying arbitrary code
needs a per-project fixtures provider — a clear next design step, out of
scope here.

**Evidence:** `tests/test_cli_path.py` — 6 tests via `click.testing.CliRunner`:
the no-`--path` default still prints the exact
`Scanned migrationguard/demo/legacy_app.py: 7 finding(s), 5 auto-fixed.`
line REPRODUCTION.md documents; a two-file directory aggregates to 2
findings with an empty `verifications` map and the unverified note
present; same-name/same-line functions in two files get distinct
`file::id` keys; a directory whose only file is already-parameterised
produces `0 finding(s)` and a report containing "No risky SQL
construction patterns were found"; a syntactically broken file alongside
a good one is skipped, not fatal.

## 11. Real per-run LLM cost estimate

`trajectories.jsonl` already recorded `input_tokens` / `output_tokens`
per call; nothing added them up. `migrationguard/cost.py` now totals them
and multiplies by a published per-million-token rate (a hand-entered
constant, `PRICES`, sourced from Anthropic's pricing page and dated in
the module docstring — the one thing here that can silently go stale).
The CLI's final line gains an `LLM cost: ~$X over N priced call(s) (…
tokens)` summary for real advanced-mode runs; `run.jsonl` gets the same
as a structured record. Unknown models are surfaced ("no rate on file
for …") instead of being silently priced at zero.

Baseline mode and `--fake-llm` make no billable calls, so `cost.is_billable`
is false for them and the CLI output is byte-for-byte unchanged — the
`--fake-llm` client is counted but explicitly free.

**Evidence:** `tests/test_cost.py` — 5 tests: a missing/empty file is
zero cost and not billable; `fake-llm-client` entries are counted but
never billable (so 4a's output stays identical); a hand-computed
1M-input + 0.5M-output total matches `usd` exactly against the rate in
`PRICES`; an unrecognised model lands in `unpriced_models` and shows "no
rate on file" rather than $0.00; malformed JSONL lines are skipped.

**The advanced-mode cost/latency numbers in this repo are still from the
`--fake-llm` dry run** — no `ANTHROPIC_API_KEY` was available in the
environment this pass ran in. `REPRODUCTION.md` §4b now states exactly
which command produces the real numbers and which fields to read.

## 12. Retry / backoff and honest failure handling around real Claude calls

Before: one `RateLimitError` or dropped connection during a real
advanced-mode run raised straight out of `AnthropicLLMClient.complete()`
and aborted the entire scan — after however many findings had already
been processed and paid for. `trajectories.jsonl` would end mid-run with
no indication why.

Now `complete()` wraps the call in bounded exponential backoff with
jitter (`max_attempts=5`, on top of the SDK's own retries), classifying
each exception as *retry* (connection error, timeout, 408/409/429/5xx/529),
*degrade* (a non-retryable 4xx like a malformed request), or *raise* (an
auth / permission error — nothing downstream will work, so fail fast with
`LLMCallError`). A call that exhausts its retries **degrades that one
finding** instead of crashing: it records the reason in
`client.failures`, writes a trajectory entry whose `response` is
`<CALL FAILED after N attempt(s) -- ...>` (so the disclosure file shows
the gap), and returns `""`. The existing call sites already handle an
empty response — `scanner/explain.py` falls back to the canned
explanation and confidence, `fixgen/advanced.py` reports the finding as
not-auto-fixed, `verifier/rationale.py` falls back to the baseline
heuristic — and the CLI adds a run-level note to the report's "What we're
not confident about" section listing every degraded call.

**Evidence:** `tests/test_llm_retry.py` — 5 tests with an injected fake
transport (no network, no key): succeeds on the 3rd attempt after 2
transient failures with exactly 2 backoff sleeps and a clean trajectory
line; caps at `max_attempts` calls then returns `""` with the failure
recorded and a `<CALL FAILED ...>` trajectory line; a non-retryable
verdict degrades on the first attempt with zero sleeps; an auth verdict
raises `LLMCallError` on the first attempt; and `_classify()` maps real
`anthropic.APITimeoutError` / `RateLimitError` / `InternalServerError` /
`BadRequestError` / `AuthenticationError` instances to
retry / retry / retry / degrade / raise respectively.

## 13. Wider edge-case coverage: curated inputs, source shapes, empty runs

The curated adversarial-string list in `verifier/testgen.py` grew from 20
to 27, adding seven axes it was missing: the SQL quote-escaping sequence
`''`, a bare `?` (bind-placeholder confusion), `{}` and `%s`
(format-token confusion, so a `str.format` / `%`-format fix that leaked a
brace or token would be caught), the `NULL` keyword, a quote-free boolean
expression (`5 > 3 OR 1=1`), and a `\r\n` CRLF (the list previously only
had a bare `\n`). These are applied per parameter exactly as before, so
the baseline demo run's deterministic counts moved together:
**125 → 167 test cases, 92 → 129 identical, 33 → 38 breaking**
(0 cosmetic throughout). Most of the new strings have no way to break out
of a quoted context, so they land as *identical* — which is the point:
they widen the "provably unchanged" evidence, not just the divergence
count. `REPRODUCTION.md` §3 updated to the new numbers.

Coverage added for source *shapes* the analyzer always handled but had no
explicit test for: triple-quoted f-strings, implicitly-concatenated
multi-line f-string parts (Python fuses them into one `JoinedStr`), and a
risky `executemany()` call. Plus the two awkward run states: a scan that
finds nothing (`0 finding(s)`, and the report shows "No risky SQL
construction patterns were found" rather than an empty section or a
crash) and a run-level caveat with no per-finding fix failure behind it.

**Evidence:** +12 tests. `tests/test_queryexpr.py` (+2: triple-quoted,
implicit concat), `tests/test_scanner.py` (+2: `executemany`,
triple-quoted end to end), `tests/test_testgen.py` (new, 3: curated-list
shape + no-dupes + a guaranteed-non-matching input verified `identical`
with `[] == []`), `tests/test_report_generator.py` (new, 3: empty
`RunReport` renders, run-level notes surface, the expectedness heuristic).
Full suite: 72 passed.

---

## Main failure mode

The severity model has exactly three verdict buckets (`identical` / `cosmetic` / `breaking`) and, deliberately, no fourth bucket for "breaking because the fix is *supposed* to change this input's behavior." That interpretation lives one layer up, in the report (a heuristic in baseline mode, an LLM sentence in advanced mode) — never inside the trusted verdict itself. On this demo's migration class that's the right call: folding "is this expected" into the ground-truth verdict would mean trusting a heuristic or a model to decide what counts as a bug, which is exactly the kind of unverifiable confidence this project exists to replace. But it does mean the report currently asks a reviewer to read two things together (the verdict, and its interpretation) rather than one, and on a migration class where divergence is never intentional, that second layer would be pure overhead rather than the load-bearing distinction it is here.

## Hot take

The more interesting failure mode this build surfaced wasn't in the *migration* being verified — it was in the verifier itself, twice (entries #2 and #6). Both bugs looked fine on a casual read of the diff. Both only surfaced because something downstream refused to accept a suspicious-looking result at face value: a test suite in one case, a report number that was too clean to be true (100% divergence) in the other. That's the actual argument for a tool like this one, applied recursively: "looks right, passes the obvious check" wasn't good enough for the AI-migrated code MigrationGuard exists to verify, and it wasn't good enough for MigrationGuard's own code either. A migration-verification tool that hasn't had its own logic held to the same standard it demands of the code it checks doesn't get to claim the standard is achievable.
