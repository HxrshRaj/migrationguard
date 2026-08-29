"""CLI entrypoint. Ties scanner -> fixgen -> verifier -> report together
for both modes, and writes the two disclosure/audit artifacts every run
produces: run.jsonl (structured stage log) and trajectories.jsonl (every
LLM call made, if any -- empty in baseline mode).
"""
from __future__ import annotations

import ast
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import click

from migrationguard.cost import estimate_run_cost, format_cost_summary
from migrationguard.demo import legacy_app
from migrationguard.fixgen import advanced as fixgen_advanced
from migrationguard.fixgen import baseline as fixgen_baseline
from migrationguard.llm import (
    AnthropicLLMClient,
    FakeLLMClient,
    LatentStackLLMClient,
    LLMClient,
    TrajectoryLog,
)
from migrationguard.logging_setup import configure_logging
from migrationguard.models import FixCandidate, Mode, RunReport, VerificationResult
from migrationguard.report.generator import render_report
from migrationguard.scanner import explain as scanner_explain
from migrationguard.scanner.detector import scan_source
from migrationguard.verifier.harness import load_function
from migrationguard.verifier.rationale import annotate as annotate_rationale
from migrationguard.verifier.runner import verify_advanced, verify_baseline

DEMO_FILE = "migrationguard/demo/legacy_app.py"
_DEMO_FILE_ABS = Path(legacy_app.__file__).resolve()

_SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env", ".mypy_cache",
    ".pytest_cache", ".hypothesis", "build", "dist", ".tox", "node_modules",
}


@click.group()
def main() -> None:
    """MigrationGuard: a neutral behavioral verifier for AI-migrated code.

    MigrationGuard doesn't migrate your code -- it proves whether the
    migration you already have is safe to ship.
    """


@main.command()
@click.option(
    "--mode",
    type=click.Choice(["baseline", "advanced"]),
    default="baseline",
    show_default=True,
)
@click.option(
    "--provider",
    type=click.Choice(["anthropic", "latentstack"]),
    default="anthropic",
    show_default=True,
    help="LLM provider for advanced mode.",
)
@click.option(
    "--path",
    "scan_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="File or directory to scan. Default: the bundled demo app. A "
    "directory is walked recursively for .py files; behavioral "
    "verification only runs for the bundled demo (see README).",
)
@click.option(
    "--out-dir", type=click.Path(path_type=Path), default=Path("out"), show_default=True
)
@click.option(
    "--max-examples",
    type=int,
    default=200,
    show_default=True,
    help="Hypothesis example budget per finding (advanced mode only).",
)
@click.option(
    "--seed",
    type=int,
    default=20260830,
    show_default=True,
    help="Hypothesis seed, for a deterministic, reproducible advanced-mode run.",
)
@click.option(
    "--fake-llm",
    is_flag=True,
    help="Use a deterministic fake LLM client instead of calling Claude "
    "(advanced mode only) -- for a dry run with no ANTHROPIC_API_KEY set.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="When set, print the full RunReport as JSON to stdout instead of the human summary block. The other files are written as normal. The summary goes to the logger only.",
)
@click.option(
    "--fail-on",
    type=click.Choice(["none", "breaking"]),
    default="none",
    show_default=True,
    help="If 'breaking', exit with code 1 if any verification has breaking > 0. Default 'none' keeps exit code 0.",
)
def scan(
    mode: str,
    scan_path: Path | None,
    out_dir: Path,
    max_examples: int,
    seed: int,
    fake_llm: bool,
    json_output: bool,
    fail_on: str,
    provider: str,
) -> None:
    """Scan code for risky SQL construction, fix what it can, verify every fix."""
    mode_enum = Mode(mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    # In --json mode stdout must be pure JSON, so keep logging to the file
    # only; the human-readable console stream would otherwise pollute it.
    logger = configure_logging(out_dir / "run.jsonl", also_console=not json_output)
    trajectory_log = TrajectoryLog(out_dir / "trajectories.jsonl")
    trajectory_log.path.write_text("", encoding="utf-8")  # start this run's log fresh

    files = _resolve_files(scan_path)
    scan_label = DEMO_FILE if scan_path is None else str(scan_path)
    logger.info(
        f"starting scan mode={mode} out_dir={out_dir} "
        f"path={scan_label} files={len(files)}"
    )

    disambiguate = len(files) > 1
    all_findings = []
    source_by_file: dict[str, str] = {}
    for file_path in files:
        display = _display_path(file_path)
        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:  # unreadable file: log and skip
            logger.info(f"skipped {display}: {exc}")
            continue
        try:
            file_findings = scan_source(source, display)
        except SyntaxError as exc:  # not valid Python 3: log and skip
            logger.info(f"skipped {display}: syntax error ({exc})")
            continue
        source_by_file[display] = source
        for finding in file_findings:
            if disambiguate:
                finding = finding.model_copy(update={"id": f"{display}::{finding.id}"})
            all_findings.append(finding)

    findings = all_findings
    logger.info(f"scanner found {len(findings)} risky pattern(s) across {len(source_by_file)} file(s)")

    llm: LLMClient | None = None
    if mode_enum == Mode.ADVANCED:
        if fake_llm:
            llm = FakeLLMClient(trajectory_log, responder=_fake_responder)
        elif provider == "latentstack":
            llm = LatentStackLLMClient(trajectory_log)
        else:
            llm = AnthropicLLMClient(trajectory_log)
        
        findings = [
            scanner_explain.explain(
                f, _function_source(source_by_file[f.file], f.function), llm
            )
            for f in findings
        ]
        logger.info("advanced-mode explanations generated for every finding")

    fixes: dict[str, FixCandidate] = {}
    verifications: dict[str, VerificationResult] = {}
    unverified: list[str] = []

    for finding in findings:
        source = source_by_file[finding.file]
        fix = fixgen_baseline.generate_fix(source, finding)
        if mode_enum == Mode.ADVANCED and not fix.success:
            assert llm is not None  # always constructed in advanced mode
            logger.info(
                f"{finding.function}: baseline template couldn't fix it "
                f"({fix.failure_reason}) -- escalating to the LLM fixer"
            )
            fix = fixgen_advanced.generate_fix(source, finding, llm)
        fixes[finding.id] = fix
        logger.info(
            f"{finding.function}: fix {'succeeded' if fix.success else 'failed'} "
            f"via {fix.strategy}"
        )

        if not fix.success:
            continue

        if not _is_demo_finding(finding):
            # Scan + fix generalise to any file; behavioural verification
            # needs a way to *exercise* the code (a seeded DB with a
            # matching schema), which the bundled fixtures only provide
            # for the demo app. Report the proposed fix, don't pretend
            # it was proven.
            unverified.append(f"{finding.function} ({finding.file})")
            logger.info(
                f"{finding.function}: fix generated but not verified "
                f"(no bundled fixtures for {finding.file})"
            )
            continue

        assert fix.fixed_source is not None  # success == fixed_source is set
        original_func = getattr(legacy_app, finding.function)
        fixed_func = load_function(fix.fixed_source, finding.function, {"sqlite3": sqlite3})

        if mode_enum == Mode.BASELINE:
            result = verify_baseline(finding.id, original_func, fixed_func)
        else:
            assert llm is not None  # always constructed in advanced mode
            result = verify_advanced(
                finding.id, original_func, fixed_func, max_examples=max_examples, seed=seed
            )
            result.cases = [annotate_rationale(c, llm) for c in result.cases]

        verifications[finding.id] = result
        logger.info(
            f"{finding.function}: verified {result.total_cases} case(s) -- "
            f"{result.identical} identical, {result.cosmetic} cosmetic, "
            f"{result.breaking} breaking"
        )

    notes: list[str] = []
    llm_failures = list(getattr(llm, "failures", []) or [])
    if llm_failures:
        logger.info(f"{len(llm_failures)} LLM call(s) failed after retries: {llm_failures}")
        notes.append(
            f"{len(llm_failures)} LLM call(s) failed after retries and were "
            f"degraded (canned explanation / no auto-fix / heuristic rationale "
            f"in place of the model's): " + "; ".join(llm_failures)
        )
    if unverified:
        notes.append(
            f"{len(unverified)} finding(s) outside the bundled demo app were "
            f"scanned and fix-generated but not behaviourally verified -- "
            f"MigrationGuard's harness runs against bundled fixtures and has "
            f"no seeded database for these files: " + ", ".join(sorted(unverified))
        )

    report = RunReport(
        mode=mode_enum,
        file=scan_label,
        generated_at=datetime.now(UTC).isoformat(),
        findings=findings,
        fixes=fixes,
        verifications=verifications,
        notes=notes,
    )

    (out_dir / "run_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    render_report(report, out_dir / "report.html")
    logger.info(f"wrote {out_dir / 'report.html'}")

    cost = estimate_run_cost(trajectory_log.path)
    if cost.is_billable:
        logger.info(
            f"llm cost estimate: {cost.priced_calls} priced call(s), "
            f"{cost.input_tokens}+{cost.output_tokens} tok, ~${cost.usd:.4f}"
        )

    fixed_count = sum(1 for f in fixes.values() if f.success)
    if scan_path is None:
        scanned_line = f"Scanned {DEMO_FILE}: {len(findings)} finding(s), {fixed_count} auto-fixed."
    else:
        scanned_line = (
            f"Scanned {len(source_by_file)} file(s) under {scan_label}: "
            f"{len(findings)} finding(s), {fixed_count} auto-fixed."
        )

    if json_output:
        logger.info(scanned_line)
        click.echo(report.model_dump_json(indent=2))
    else:
        lines = [
            scanned_line,
            f"Report:      {out_dir / 'report.html'}",
            f"Run log:     {out_dir / 'run.jsonl'}",
            f"Trajectories:{out_dir / 'trajectories.jsonl'}",
        ]
        if cost.is_billable:
            lines.append(format_cost_summary(cost))
        click.echo("\n".join(lines))

    if fail_on == "breaking":
        for vr in verifications.values():
            if vr.breaking > 0:
                raise SystemExit(1)


def _resolve_files(scan_path: Path | None) -> list[Path]:
    """The list of .py files a run should scan, in a stable order."""
    if scan_path is None:
        return [_DEMO_FILE_ABS]
    if scan_path.is_file():
        return [scan_path]
    out: list[Path] = []
    for p in sorted(scan_path.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return out


def _display_path(file_path: Path) -> str:
    """A short, stable label for a scanned file. The bundled demo always
    renders as its canonical repo-relative path so existing docs stay
    exact; anything else is shown relative to the current directory when
    possible, else as an absolute path."""
    resolved = file_path.resolve()
    if resolved == _DEMO_FILE_ABS:
        return DEMO_FILE
    try:
        return os.path.relpath(resolved, Path.cwd()).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return str(resolved)


def _is_demo_finding(finding) -> bool:
    return finding.file == DEMO_FILE


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ast.unparse(node)
    return ""


def _fake_responder(stage: str, system: str, prompt: str) -> str:
    """Canned, correct-by-construction replies -- lets `--fake-llm` run
    (and be demoed) end to end without ANTHROPIC_API_KEY set. See
    llm.FakeLLMClient."""
    if stage == "scanner.explain":
        return (
            "EXPLANATION: Reviewed by the fake LLM client (no API key set) "
            "-- this is a placeholder, not a real model explanation.\n"
            "CONFIDENCE: 0.7"
        )
    if stage == "fixgen.rewrite":
        if "email LIKE" in prompt:
            return (
                "```python\n"
                "def search_users_by_email_domain(conn, domain):\n"
                "    cur = conn.execute(\n"
                '        "SELECT id, name, email FROM users WHERE email LIKE ?",\n'
                '        (f"%{domain}",),\n'
                "    )\n"
                "    return cur.fetchall()\n"
                "```"
            )
        return (
            "```python\n"
            "def list_active_users_over_age(conn, min_age, role):\n"
            "    cur = conn.execute(\n"
            '        "SELECT id, name, email FROM users WHERE age > ? AND role = ?",\n'
            "        (min_age, role),\n"
            "    )\n"
            "    return cur.fetchall()\n"
            "```"
        )
    if stage == "diffengine.rationale":
        return "Placeholder rationale from the fake LLM client (no API key set)."
    return ""


if __name__ == "__main__":
    main()
