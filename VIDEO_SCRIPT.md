# MigrationGuard — solution video storyboard (≤ 5:00)

A single-take screen recording. Two windows: a **terminal** (left) and a
**browser** (right) showing a `report.html`. Every command below is
copy-paste exact. Budget: 0:45 / 1:35 / 1:35 / 0:45 with ~20s slack.

**Before you hit record** (do NOT film this):

```bash
python -m venv .venv && .venv\Scripts\activate     # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                          # expect: 87 passed
rm -rf out/                                        # so the baseline run is live on camera
```

The advanced run against the real LatentStack gateway takes ~7 minutes
(Gemini 3.1 Pro is a reasoning model), so **do not run it live**. Its
finished report and trajectory log are already committed at
`artifacts/report-advanced-latentstack.html` and
`artifacts/trajectories-latentstack.jsonl` — Section 3 points the browser
at those.

---

## 0:00 – 0:45 · The problem

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
> "Scope for this demo: one migration class end to end — unsafe
> string-formatted SQL → parameterized queries — against a bundled
> 7-function legacy app."

On screen: open `migrationguard/demo/legacy_app.py`, scroll once past
`find_user_by_name` (f-string) and `list_active_users_over_age` (query
built across two statements). ~8 seconds, then move on.

---

## 0:45 – 2:20 · Baseline run + report walkthrough

**Terminal** (say "deterministic — no LLM, no network"):

```bash
migrationguard scan --mode baseline --out-dir out/baseline
```

Read the final line:

> "Seven risky patterns, five auto-fixed by the deterministic AST
> rewriter. The other two it *declines*, with a reason — the honest move,
> and why the advanced fixer exists."

**Browser — open `out/baseline/report.html`. Three things, in order:**

1. **Executive summary strip:** "7 findings, 5 auto-fixed, 167 test cases,
   129 identical, 0 cosmetic, 38 behavioral divergences. Those 167 are the
   deterministic curated adversarial-input battery, applied per parameter."
2. **"What we're not confident about":** "`search_users_by_email_domain`
   and `list_active_users_over_age` — the two the template can't safely
   rewrite. Watch these; they disappear in advanced mode."
3. **`get_user_by_id_unsafe` card → "Smallest input that diverges" + the
   per-case table:** "Every divergence traces to a SQL metacharacter. The
   Interpretation column flags each as 'likely expected — adversarial' vs
   'review needed'. The *verdict* — `identical` / `breaking` — is pure and
   deterministic; that interpretation is a separate labeled layer, never
   folded into the verdict. That separation is the project's main design
   decision."

---

## 2:20 – 3:55 · Advanced run (real) + what changed

**Terminal — show the command, then say it was run off-camera:**

```bash
export LATENTSTACK_API_KEY=ls-...
migrationguard scan --mode advanced --provider latentstack \
  --out-dir out/advanced --max-examples 150 --seed 20260830
```

> "Advanced mode: every stage that can use a model, does — a
> context-specific explanation per finding, an LLM rewrite for the two the
> template declined, a written rationale per divergence. This one ran
> against the **LatentStack gateway**, model Gemini 3.1 Pro. It takes about
> seven minutes, so here's the finished result."

**Terminal — the disclosure artifact:**

```bash
wc -l artifacts/trajectories-latentstack.jsonl
head -c 400 artifacts/trajectories-latentstack.jsonl ; echo
```

> "Thirty-nine calls — seven explanations, two fixes, thirty rationales.
> Every line: prompt, response, model, tokens, latency. Produced
> automatically by the one LLM client wrapper, never assembled after the
> fact. Zero failed calls."

**Browser — open `artifacts/report-advanced-latentstack.html`. Two things:**

1. **Executive summary + "What we're not confident about":** "7 of 7
   auto-fixed now — the two baseline couldn't touch are gone, both fixed by
   the LLM rewriter, which reads the whole function and self-checks the
   parameter list. 683 verification cases: 473 identical, 210 breaking,
   every breaking one on adversarial input. Cost: about 31 cents, roughly
   seven minutes."
2. **`list_active_users_over_age` card:** "Hundreds of Hypothesis-generated
   cases, type-aware, shrunk to a minimal counterexample. Same
   deterministic verdict engine as baseline — advanced mode changed the
   *inputs* and the *narration*, not the scoring."

---

## 3:55 – 4:40 · Changelog highlights + hot take

**Editor — `CHANGELOG.md`.** Scroll to bug entries #2 and #6, then the
LatentStack entries (16–19), then "Hot take".

> "The changelog is evidence-tied, one entry per iteration. Two of them are
> bugs found in *MigrationGuard itself*: #2, quotes left around a bind
> placeholder so it silently returned zero rows; #6, a `__future__` import
> turning every type hint into a string so type-aware test-gen silently
> degraded to a 100% false-divergence rate. Neither looked wrong on a
> casual read — each was only caught because something downstream refused a
> too-clean result. That's the argument for this tool, applied to its own
> code."
>
> "Entries 16 through 19 were built with the LatentStack coding agent —
> three merged PRs adding a fifth detected pattern, CI flags, and
> LatentStack itself as a second LLM provider — plus the real advanced run
> above, on the LatentStack gateway."

---

## 4:40 – 5:00 · Close

> "Scan, fix, and *prove* — or say exactly where the proof runs out.
> `--path` points it at any codebase; the verifier is the reusable part.
> Everything's in the repo: reproduction guide, the disclosed trajectories,
> a changelog that shows the work."

Last frame: the `artifacts/report-advanced-latentstack.html` summary strip.

---

### Fallbacks

- Baseline run is < 1 s. If you'd rather show advanced *live* without a
  key: `migrationguard scan --mode advanced --fake-llm --out-dir out/advanced
  --max-examples 150` runs in a few seconds and hits 7/7 — say "deterministic
  stand-in, tagged `fake-llm-client`" and skip the cost line.
- `pytest -q` on a cold Windows checkout takes ~1–2 min; run it off-camera.
