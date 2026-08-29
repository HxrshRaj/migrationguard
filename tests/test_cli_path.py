"""`migrationguard scan --path ...` -- the generalisation from "only ever
scans its own bundled demo" to "scans any file or directory of .py code".

The default (no --path) must stay byte-for-byte what REPRODUCTION.md
documents; everything else is additive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from click.testing import CliRunner

from migrationguard.models import RunReport
from migrationguard.orchestrator.cli import main

RISKY_FSTRING = (
    "import sqlite3\n"
    "def {name}(conn, who):\n"
    "    q = f\"SELECT * FROM t WHERE who = '{{who}}'\"\n"
    "    return conn.execute(q).fetchall()\n"
)
CLEAN = (
    "import sqlite3\n"
    "def safe_lookup(conn, who):\n"
    "    return conn.execute('SELECT * FROM t WHERE who = ?', (who,)).fetchall()\n"
)


def _run(*args: str):
    return CliRunner().invoke(main, ["scan", *args])


def _load_report(out_dir) -> RunReport:
    return RunReport.model_validate_json((out_dir / "run_report.json").read_text())


def test_default_path_scans_the_demo_and_keeps_the_documented_summary(tmp_path):
    result = _run("--mode", "baseline", "--out-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert (
        "Scanned migrationguard/demo/legacy_app.py: 7 finding(s), 5 auto-fixed."
        in result.output
    )
    assert (tmp_path / "report.html").exists()


def test_directory_scan_aggregates_findings_across_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(RISKY_FSTRING.format(name="lookup_a"), encoding="utf-8")
    (src / "b.py").write_text(RISKY_FSTRING.format(name="lookup_b"), encoding="utf-8")
    out = tmp_path / "out"

    result = _run("--mode", "baseline", "--path", str(src), "--out-dir", str(out))
    assert result.exit_code == 0, result.output
    assert "Scanned 2 file(s)" in result.output
    assert "2 finding(s)" in result.output

    report = _load_report(out)
    assert len(report.findings) == 2
    assert {f.function for f in report.findings} == {"lookup_a", "lookup_b"}
    # not the bundled demo -> scanned + fix-generated but not behaviourally verified
    assert report.verifications == {}
    assert report.notes and "not behaviourally verified" in report.notes[0]


def test_finding_ids_are_disambiguated_across_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # identical function name at the identical line in two files -> identical
    # base id ("lookup#2"); the run must still key them separately.
    (src / "one.py").write_text(RISKY_FSTRING.format(name="lookup"), encoding="utf-8")
    (src / "two.py").write_text(RISKY_FSTRING.format(name="lookup"), encoding="utf-8")
    out = tmp_path / "out"

    result = _run("--mode", "baseline", "--path", str(src), "--out-dir", str(out))
    assert result.exit_code == 0, result.output
    report = _load_report(out)
    ids = [f.id for f in report.findings]
    assert len(ids) == len(set(ids)) == 2
    assert all("::" in i for i in ids)


def test_zero_findings_still_renders_a_sane_report(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.py").write_text(CLEAN, encoding="utf-8")
    out = tmp_path / "out"

    result = _run("--mode", "baseline", "--path", str(src), "--out-dir", str(out))
    assert result.exit_code == 0, result.output
    assert "0 finding(s), 0 auto-fixed" in result.output
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "No risky SQL construction patterns were found" in html
    report = _load_report(out)
    assert report.findings == [] and report.fixes == {} and report.verifications == {}


def test_a_file_that_is_not_valid_python_is_skipped_not_fatal(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "good.py").write_text(RISKY_FSTRING.format(name="lookup_ok"), encoding="utf-8")
    (src / "broken.py").write_text("def (:\n  not python at all\n", encoding="utf-8")
    out = tmp_path / "out"

    result = _run("--mode", "baseline", "--path", str(src), "--out-dir", str(out))
    assert result.exit_code == 0, result.output
    report = _load_report(out)
    assert [f.function for f in report.findings] == ["lookup_ok"]


def test_path_to_a_single_file_works_and_is_not_disambiguated(tmp_path):
    f = tmp_path / "only.py"
    f.write_text(RISKY_FSTRING.format(name="lookup_single"), encoding="utf-8")
    out = tmp_path / "out"

    result = _run("--mode", "baseline", "--path", str(f), "--out-dir", str(out))
    assert result.exit_code == 0, result.output
    report = _load_report(out)
    assert len(report.findings) == 1
    assert "::" not in report.findings[0].id  # single file -> clean id

def test_json_flag_prints_valid_json_and_suppresses_summary(tmp_path):
    result = _run("--mode", "baseline", "--out-dir", str(tmp_path), "--json")
    assert result.exit_code == 0, result.output
    # Must not contain the human summary
    assert "Scanned migrationguard/demo/legacy_app.py" not in result.output
    # Must be valid parseable JSON for a RunReport
    report = RunReport.model_validate(json.loads(result.output))
    assert len(report.findings) == 7


def test_json_output_is_utf8_under_a_legacy_stdout_encoding(tmp_path):
    # Regression: --json used to crash with UnicodeEncodeError when stdout
    # was a non-UTF-8 pipe (e.g. cp1252 on Windows), because a curated test
    # input contains an emoji. It must write UTF-8 bytes regardless.
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    proc = subprocess.run(
        [sys.executable, "-m", "migrationguard.orchestrator.cli",
         "scan", "--mode", "baseline", "--json", "--out-dir", str(tmp_path)],
        capture_output=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    report = RunReport.model_validate_json(proc.stdout.decode("utf-8"))
    assert len(report.findings) == 7

def test_fail_on_breaking_gives_exit_code_1_for_demo(tmp_path):
    # The bundled demo app has breaking divergences
    result = _run("--mode", "baseline", "--out-dir", str(tmp_path), "--fail-on", "breaking")
    assert result.exit_code == 1, result.output
    assert "Scanned migrationguard/demo/legacy_app.py" in result.output

def test_fail_on_none_gives_exit_code_0_for_demo(tmp_path):
    # Default behavior should be exit code 0 even with breaking divergences
    result = _run("--mode", "baseline", "--out-dir", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Scanned migrationguard/demo/legacy_app.py" in result.output

