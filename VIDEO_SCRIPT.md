# MigrationGuard — solution video storyboard (≤ 5:00)

A single-take screen recording. Two windows only: a **terminal** (left) and a
**browser** (right) showing `out/<mode>/report.html`. Every command below is
copy-paste exact. Total budget is 5:00; the four sections are 0:45 / 1:35 /
1:35 / 0:45 with ~20s of slack.

**Before you hit record** (do NOT film this):

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                                           # expect: 79 passed
rm -rf out/                                         # start clean so the runs are live on camera
```

If you have a key and want real Claude calls in section 3:
`export ANTHROPIC_API_KEY=sk-ant-...` now, off camera. If you don't, the
script's fallback (`--fake-llm`) is already wired in — say the one sentence
marked *[no-key]* instead of *[key]* and keep going.

---

## 0:00 – 0:45 · The problem (talking head or terminal, no commands)

> "An AI tool just migrated a legacy codebase. It compiles. It passes the
> tests someone wrote. 'Migration complete' — but that's a *claim*, not
> evidence. The bug that costs you is the input nobody tested: a name with
> an apostrophe, an empty string, an injection string that used to be
> harmless and now isn't."
>
> "MigrationGuard doesn't do the migration. It sits *downstream* of one and
> answers one question: does the fixed code behave identically to the
> original, and if not, exactly where — proven across a generated battery
> of edge cases."
>
> "Scope for this demo: one real migration class end to end — unsafe
> string-formatted SQL → parameterized queries — against a bundled 7-function
> legacy app."

On screen while talking: open `migrationguard/demo/legacy_app.py`, scroll
once past `find_user_by_name` (f-string) and `list_active_users_over_age`
(query built across two statements). ~8 seconds, then move on.

---

## 0:45 – 2:20 · Baseline run + report walkthrough (~1:35)

**Terminal (say "deterministic — no LLM, no network"):**

```bash
migrationguard scan --mode baseline --out-dir out/baseline
```

Point at the final summary line as you read it:

> "Seven risky patterns found, five auto-fixed by the deterministic AST
> rewriter. The other two it *declines* — with a reason — which is the
> honest move, and it's why the advanced fixer exists."

**Browser — open `out/baseline/report.html`. Hit three things, in order:**

1. **Executive summary strip (top):** "7 findings, 5 auto-fixed, 167 test
   cases run, 129 identical, 0 cosmetic, 38 behavioral divergences. Those
   167 cases are the deterministic curated adversarial-input battery,
   applied per parameter."
2. **"What we're not confident about":** "`search_users_by_email_domain`
   and `list_active_users_over_age` — the two the template can't safely
   rewrite. Note them; they should disappear in advanced mode."
3. **`get_user_by_id_unsafe` card → "Smallest input that diverges" + the
   per-case table:** "Every divergence traces to a SQL metacharacter. The
   heuristic in the Interpretation column flags each one as 'likely
   expected — adversarial input' vs 'review needed'. The *verdict*
   (`identical`/`breaking`) is pure and deterministic; that interpretation
   is a separate, clearly-labeled layer on top — never folded into the
   verdict. That separation is the project's main design decision."

---

## 2:20 – 3:55 · Advanced run + what changed (~1:35)

**Terminal:**

*[key]* "Same command, `--mode advanced` — now every stage that can use a
model, does: a context-specific explanation per finding, an LLM rewrite for
the two the template declined, and a written rationale per divergence.
Every one of those calls is logged."

```bash
migrationguard scan --mode advanced --out-dir out/advanced --max-examples 150 --seed 20260830
```

*[no-key]* say instead: "No API key here, so `--fake-llm` — a deterministic
stand-in that exercises the exact same pipeline shape, including the
LLM-escalation path. Every call is still logged, tagged `fake-llm-client`
so a dry run is never mistaken for real output."

```bash
migrationguard scan --mode advanced --fake-llm --out-dir out/advanced --max-examples 150
```

Read the final lines: "7 of 7 auto-fixed now." *[key]* also point at the
`LLM cost: ~$… over N priced call(s)` line — "real token counts from the
trajectory log, priced at the published rate."

**Terminal — show the disclosure artifact:**

```bash
head -c 400 out/advanced/trajectories.jsonl ; echo
wc -l out/advanced/trajectories.jsonl
```

> "This file is the agent-trajectory disclosure — prompt, response, model,
> tokens, latency, one JSON object per call. Produced automatically, never
> assembled after the fact."

**Browser — reload `out/advanced/report.html`, hit two things:**

1. **"What we're not confident about":** "The two findings from baseline
   are gone — both fixed by the LLM rewriter, which reads the whole
   function instead of one expression, and self-checks the parameter list
   before accepting."
2. **`list_active_users_over_age` card:** "Hundreds of Hypothesis-generated
   cases, type-aware, and the 'Smallest input that diverges' line is
   auto-shrunk to a minimal counterexample. Same deterministic verdict
   engine underneath — advanced mode changed the *inputs* and the
   *narration*, not the scoring."

---

## 3:55 – 4:40 · Changelog highlights + hot take (~0:45)

**Browser or editor — `CHANGELOG.md`.** Scroll to the two bug entries
(#2 and #6) and the "Hot take".

> "The changelog is evidence-tied — an entry per iteration. The two most
> interesting entries are bugs found in *MigrationGuard itself* during the
> build: #2, quotes left around a bind placeholder so it silently returned
> zero rows; #6, `from __future__ import annotations` turning every type
> hint into a string so type-aware test-gen silently degraded to a 100%
> false-divergence rate."
>
> "Neither looked wrong on a casual read. Both were only caught because
> something downstream refused a suspicious result at face value — a test
> in one case, a report number too clean to be true in the other. That's
> the whole argument for this tool, applied to its own code: 'looks right,
> passes the obvious check' wasn't good enough for the migrated code, and
> it wasn't good enough here either."

---

## 4:40 – 5:00 · Close (~0:20)

> "Scan, fix, and *prove* — or say exactly where the proof runs out.
> `--path` points it at any codebase; the verifier is the reusable part.
> Everything's in the repo: reproduction guide, the disclosed trajectories,
> and a changelog that shows the work."

Last frame: the `out/advanced/report.html` executive-summary strip.

---

### If a run is slower than expected on camera

Baseline is < 1s. Advanced `--fake-llm` is a few seconds (Hypothesis
shrinking, not model latency). Real advanced mode is a few minutes for the
full demo app — if filming with a key, **run it once off-camera first** so
`out/advanced/` already exists, then on camera re-run it and cut to the
finished report, or lower `--max-examples` to `60`.
