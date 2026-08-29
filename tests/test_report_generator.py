"""The HTML report renders for the awkward inputs: nothing found at all,
and a run-level caveat with no per-finding fix failure."""
from __future__ import annotations

from migrationguard.models import Mode, RunReport
from migrationguard.report.generator import heuristic_expectedness, render_report


def test_renders_an_empty_run_report_without_crashing(tmp_path):
    report = RunReport(
        mode=Mode.BASELINE, file="some/dir", generated_at="2026-08-29T00:00:00+00:00"
    )
    out = tmp_path / "report.html"
    render_report(report, out)
    html = out.read_text(encoding="utf-8")

    assert "No risky SQL construction patterns were found" in html
    assert "the scan found no risky patterns" in html
    assert "<div class=\"n\">0</div>" in html  # summary stats present, all zero


def test_run_level_notes_appear_in_the_not_confident_section(tmp_path):
    report = RunReport(
        mode=Mode.BASELINE,
        file="some/dir",
        generated_at="2026-08-29T00:00:00+00:00",
        notes=["3 finding(s) outside the bundled demo app were not verified."],
    )
    out = tmp_path / "report.html"
    render_report(report, out)
    html = out.read_text(encoding="utf-8")
    assert "outside the bundled demo app were not verified" in html


def test_heuristic_expectedness_flags_metacharacters():
    assert "likely expected" in heuristic_expectedness("(\"O'Brien\",)")
    assert "likely expected" in heuristic_expectedness("('a', '-- x')")
    # no SQL metacharacter in the repr at all -> the heuristic asks for review
    assert "review needed" in heuristic_expectedness("(42,)")
