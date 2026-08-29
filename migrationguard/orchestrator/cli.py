"""CLI entrypoint. Ties scanner -> fixgen -> verifier -> report together
for both modes, and writes the two disclosure/audit artifacts every run
produces: run.jsonl (structured stage log) and trajectories.jsonl (every
LLM call made, if any -- empty in baseline mode).
"""
from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click

from migrationguard.demo import legacy_app
from migrationguard.fixgen import advanced as fixgen_advanced
from migrationguard.fixgen import baseline as fixgen_baseline
from migrationguard.llm import AnthropicLLMClient, FakeLLMClient, TrajectoryLog
from migrationguard.logging_setup import configure_logging
from migrationguard.models import FixCandidate, Mode, RunReport, VerificationResult
from migrationguard.report.generator import render_report
from migrationguard.scanner import explain as scanner_explain
from migrationguard.scanner.detector import scan_source
from migrationguard.verifier.harness import load_function
from migrationguard.verifier.rationale import annotate as annotate_rationale
from migrationguard.verifier.runner import verify_advanced, verify_baseline

DEMO_FILE = "migrationguard/demo/legacy_app.py"


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
def scan(mode: str, out_dir: Path, max_examples: int, seed: int, fake_llm: bool) -> None:
    """Scan the bundled demo app, fix what it can, verify every fix."""
    mode_enum = Mode(mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(out_dir / "run.jsonl")
    trajectory_log = TrajectoryLog(out_dir / "trajectories.jsonl")
    trajectory_log.path.write_text("", encoding="utf-8")  # start this run's log fresh

    logger.info(f"starting scan mode={mode} out_dir={out_dir}")

    source = Path(legacy_app.__file__).read_text(encoding="utf-8")
    findings = scan_source(source, DEMO_FILE)
    logger.info(f"scanner found {len(findings)} risky pattern(s)")

    llm = None
    if mode_enum == Mode.ADVANCED:
        llm = (
            FakeLLMClient(trajectory_log, responder=_fake_responder)
            if fake_llm
            else AnthropicLLMClient(trajectory_log)
        )
        findings = [
            scanner_explain.explain(f, _function_source(source, f.function), llm)
            for f in findings
        ]
        logger.info("advanced-mode explanations generated for every finding")

    fixes: dict[str, FixCandidate] = {}
    verifications: dict[str, VerificationResult] = {}

    for finding in findings:
        fix = fixgen_baseline.generate_fix(source, finding)
        if mode_enum == Mode.ADVANCED and not fix.success:
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

        original_func = getattr(legacy_app, finding.function)
        fixed_func = load_function(fix.fixed_source, finding.function, {"sqlite3": sqlite3})

        if mode_enum == Mode.BASELINE:
            result = verify_baseline(finding.id, original_func, fixed_func)
        else:
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

    report = RunReport(
        mode=mode_enum,
        file=DEMO_FILE,
        generated_at=datetime.now(timezone.utc).isoformat(),
        findings=findings,
        fixes=fixes,
        verifications=verifications,
    )

    (out_dir / "run_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    render_report(report, out_dir / "report.html")
    logger.info(f"wrote {out_dir / 'report.html'}")

    fixed_count = sum(1 for f in fixes.values() if f.success)
    click.echo(
        f"Scanned {DEMO_FILE}: {len(findings)} finding(s), {fixed_count} auto-fixed.\n"
        f"Report:      {out_dir / 'report.html'}\n"
        f"Run log:     {out_dir / 'run.jsonl'}\n"
        f"Trajectories:{out_dir / 'trajectories.jsonl'}"
    )


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
