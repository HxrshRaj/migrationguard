"""Renders a RunReport to a single self-contained, audit-ready HTML file.

No CDN dependency -- the report has to open and render correctly offline,
since reproduction shouldn't require network access to *view* the result.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, select_autoescape

from migrationguard.models import RunReport, Severity

_SQL_METACHARACTERS = ("'", '"', "--", "/*", ";", " OR ", "DROP ")


def heuristic_expectedness(input_repr: str) -> str:
    """A cheap, non-LLM heuristic used in baseline mode (and as a
    cross-check in advanced mode): does this divergence's input look
    adversarial? This is presentation-layer interpretation, not a verdict
    -- diffengine.classify() never sees or uses this."""
    if any(marker in input_repr for marker in _SQL_METACHARACTERS):
        return "likely expected — adversarial input, consistent with the fix correctly changing unsafe-input handling"
    return "review needed — no obvious SQL metacharacter in this input"


def render_report(report: RunReport, out_path: Path) -> None:
    env = Environment(autoescape=select_autoescape(["html"]))
    env.filters["expectedness"] = heuristic_expectedness
    template = env.from_string(_TEMPLATE)

    summary = _summarize(report)
    low_confidence = [
        f for f in report.findings if report.fixes.get(f.id, None) is None or report.fixes[f.id].confidence < 0.7
    ]
    unfixed = [f for f in report.findings if not report.fixes.get(f.id) or not report.fixes[f.id].success]
    files_scanned = sorted({f.file for f in report.findings})

    html = template.render(
        report=report,
        summary=summary,
        low_confidence=low_confidence,
        unfixed=unfixed,
        files_scanned=files_scanned,
        multi_file=len(files_scanned) > 1,
        Severity=Severity,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def _summarize(report: RunReport) -> dict:
    fixed = sum(1 for fx in report.fixes.values() if fx.success)
    total_cases = sum(v.total_cases for v in report.verifications.values())
    identical = sum(v.identical for v in report.verifications.values())
    cosmetic = sum(v.cosmetic for v in report.verifications.values())
    breaking = sum(v.breaking for v in report.verifications.values())
    return {
        "total_findings": len(report.findings),
        "fixed": fixed,
        "unfixed": len(report.findings) - fixed,
        "total_cases": total_cases,
        "identical": identical,
        "cosmetic": cosmetic,
        "breaking": breaking,
    }


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MigrationGuard Report — {{ report.file }}</title>
<style>
  :root{
    --ink:#161b18; --ink-soft:#4b564f; --paper:#f5f6f3; --card:#ffffff;
    --line:#d8ded7; --accent:#2f6f4f; --accent-soft:#e4efe7;
    --warn:#b8542f; --warn-soft:#f5e6dc; --mono-bg:#161b18; --mono-fg:#e9ede9;
  }
  *{box-sizing:border-box;}
  body{background:var(--paper); color:var(--ink); font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:36px 20px 90px;}
  .wrap{max-width:960px; margin:0 auto;}
  h1{font-size:26px; margin:0 0 4px;}
  .sub{color:var(--ink-soft); font-size:14px; margin-bottom:28px;}
  .grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:32px;}
  .stat{background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px;}
  .stat .n{font-size:24px; font-weight:700; font-variant-numeric:tabular-nums;}
  .stat .l{font-size:11px; letter-spacing:.05em; text-transform:uppercase; color:var(--ink-soft);}
  .stat.breaking .n{color:var(--warn);}
  .stat.identical .n{color:var(--accent);}
  section{margin-bottom:34px;}
  h2{font-size:18px; border-bottom:1px solid var(--line); padding-bottom:8px;}
  .card{background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px 18px; margin-bottom:16px;}
  .card h3{margin:0 0 4px; font-size:16px; font-family:monospace;}
  .badge{display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:5px; margin-left:6px;}
  .badge.ok{background:var(--accent-soft); color:var(--accent);}
  .badge.fail{background:var(--warn-soft); color:var(--warn);}
  .badge.pattern{background:var(--line); color:var(--ink-soft); font-family:monospace;}
  pre{background:var(--mono-bg); color:var(--mono-fg); padding:10px 12px; border-radius:8px; overflow-x:auto; font-size:12.5px;}
  table{width:100%; border-collapse:collapse; font-size:13px; margin-top:10px;}
  th,td{text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);}
  th{font-size:10.5px; text-transform:uppercase; color:var(--ink-soft);}
  .sev{font-weight:600;}
  .sev.identical{color:var(--accent);}
  .sev.breaking{color:var(--warn);}
  .sev.cosmetic{color:#9a7a2a;}
  .muted{color:var(--ink-soft); font-size:13px;}
  .notconfident li{margin-bottom:6px;}
</style>
</head>
<body>
<div class="wrap">
  <h1>MigrationGuard Verification Report</h1>
  <div class="sub">{{ report.file }}{% if multi_file %} · {{ files_scanned|length }} files with findings{% endif %} · mode: {{ report.mode.value }} · generated {{ report.generated_at }}</div>

  <div class="grid">
    <div class="stat"><div class="n">{{ summary.total_findings }}</div><div class="l">Risky patterns found</div></div>
    <div class="stat"><div class="n">{{ summary.fixed }}/{{ summary.total_findings }}</div><div class="l">Auto-fixed</div></div>
    <div class="stat"><div class="n">{{ summary.total_cases }}</div><div class="l">Test cases run</div></div>
    <div class="stat identical"><div class="n">{{ summary.identical }}</div><div class="l">Identical behavior</div></div>
    <div class="stat"><div class="n">{{ summary.cosmetic }}</div><div class="l">Cosmetic difference</div></div>
    <div class="stat breaking"><div class="n">{{ summary.breaking }}</div><div class="l">Behavioral divergence</div></div>
  </div>

  <section>
    <h2>What we're not confident about</h2>
    {% if unfixed or low_confidence or report.notes %}
    <ul class="notconfident">
      {% for note in report.notes %}
      <li>{{ note }}</li>
      {% endfor %}
      {% for f in unfixed %}
      <li><b>{{ f.function }}</b> — no automated fix generated ({{ report.fixes.get(f.id).failure_reason if report.fixes.get(f.id) else "not attempted" }})</li>
      {% endfor %}
      {% for f in low_confidence %}
      {% if f not in unfixed %}
      <li><b>{{ f.function }}</b> — fix confidence {{ "%.2f"|format(report.fixes[f.id].confidence) }}, below the 0.70 review threshold</li>
      {% endif %}
      {% endfor %}
    </ul>
    {% elif report.findings %}
    <p class="muted">Every finding was auto-fixed with confidence ≥ 0.70.</p>
    {% else %}
    <p class="muted">Nothing to report — the scan found no risky patterns.</p>
    {% endif %}
  </section>

  <section>
    <h2>Findings</h2>
    {% if not report.findings %}
    <p class="muted">No risky SQL construction patterns were found in the scanned code.</p>
    {% endif %}
    {% for f in report.findings %}
    <div class="card">
      <h3>{{ f.function }}()
        <span class="badge pattern">{{ f.pattern_type.value }}</span>
        {% set fix = report.fixes.get(f.id) %}
        {% if fix and fix.success %}<span class="badge ok">fixed · {{ fix.strategy }} · conf {{ "%.2f"|format(fix.confidence) }}</span>
        {% else %}<span class="badge fail">not auto-fixed</span>{% endif %}
      </h3>
      <p class="muted">{% if multi_file %}{{ f.file }} · {% endif %}line {{ f.line }} · scanner confidence {{ "%.2f"|format(f.confidence) }}</p>
      <p>{{ f.risk_explanation }}</p>
      <pre>{{ f.code_snippet }}</pre>

      {% if fix and fix.success %}
        <p class="muted">{{ fix.rationale }}</p>
      {% elif fix %}
        <p class="muted"><b>Why no fix:</b> {{ fix.failure_reason }}</p>
      {% endif %}

      {% set v = report.verifications.get(f.id) %}
      {% if v %}
      <table>
        <tr><th>Total</th><th>Identical</th><th>Cosmetic</th><th>Breaking</th></tr>
        <tr><td>{{ v.total_cases }}</td><td class="sev identical">{{ v.identical }}</td><td class="sev cosmetic">{{ v.cosmetic }}</td><td class="sev breaking">{{ v.breaking }}</td></tr>
      </table>
      {% if v.minimal_failing_example %}
      <p class="muted"><b>Smallest input that diverges:</b> <code>{{ v.minimal_failing_example }}</code></p>
      {% endif %}
      <table>
        <tr><th>Input</th><th>Severity</th><th>Interpretation</th></tr>
        {% for c in v.cases[:12] %}
        {% if c.severity != Severity.IDENTICAL %}
        <tr>
          <td><code>{{ c.input_repr }}</code></td>
          <td class="sev {{ c.severity.value }}">{{ c.severity.value }}</td>
          <td class="muted">{{ c.rationale if c.rationale else (c.input_repr | expectedness) }}</td>
        </tr>
        {% endif %}
        {% endfor %}
      </table>
      {% endif %}
    </div>
    {% endfor %}
  </section>
</div>
</body>
</html>
"""
