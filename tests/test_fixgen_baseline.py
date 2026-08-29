"""Baseline fix generator against the demo codebase: confirms exactly
which findings it can and can't fix, and that every fix it claims success
on actually parses and defines the right function."""
from __future__ import annotations

import ast
import inspect

from migrationguard.demo import legacy_app
from migrationguard.fixgen.baseline import generate_fix
from migrationguard.scanner.detector import scan_source

SOURCE = inspect.getsource(legacy_app)
FILE = inspect.getfile(legacy_app)

EXPECTED_FIXABLE = {
    "find_user_by_name",
    "find_users_by_role",
    "delete_user_by_name",
    "update_user_role",
    "get_user_by_id_unsafe",
}
EXPECTED_UNFIXABLE = {"search_users_by_email_domain", "list_active_users_over_age"}


def test_fixes_exactly_the_mechanically_fixable_findings():
    findings = scan_source(SOURCE, FILE)
    results = {f.function: generate_fix(SOURCE, f) for f in findings}

    fixable = {name for name, fix in results.items() if fix.success}
    unfixable = {name for name, fix in results.items() if not fix.success}

    assert fixable == EXPECTED_FIXABLE
    assert unfixable == EXPECTED_UNFIXABLE
    for name in unfixable:
        assert results[name].failure_reason  # every failure is explained


def test_every_successful_fix_parses_and_defines_the_right_function():
    findings = scan_source(SOURCE, FILE)
    for finding in findings:
        fix = generate_fix(SOURCE, finding)
        if not fix.success:
            continue
        tree = ast.parse(fix.fixed_source)
        assert len(tree.body) == 1
        assert isinstance(tree.body[0], ast.FunctionDef)
        assert tree.body[0].name == finding.function


def test_fixed_source_no_longer_contains_the_original_query_building_line():
    findings = scan_source(SOURCE, FILE)
    finding = next(f for f in findings if f.function == "find_user_by_name")
    fix = generate_fix(SOURCE, finding)
    assert fix.success
    assert "f\"" not in fix.fixed_source  # the f-string is gone
    assert "?" in fix.fixed_source  # replaced with a bind placeholder


_STR_FORMAT_SRC = (
    "def lookup(conn, name, role):\n"
    "    query = \"SELECT id FROM users WHERE name = '{}' AND role = '{}'\""
    ".format(name, role)\n"
    "    return conn.execute(query).fetchall()\n"
)


def test_template_fixer_rewrites_a_str_format_query():
    findings = scan_source(_STR_FORMAT_SRC, "inline.py")
    (finding,) = findings
    fix = generate_fix(_STR_FORMAT_SRC, finding)
    assert fix.success
    assert fix.strategy == "template"
    tree = ast.parse(fix.fixed_source)
    assert isinstance(tree.body[0], ast.FunctionDef)
    assert ".format(" not in fix.fixed_source  # the str.format call is gone
    assert fix.fixed_source.count("?") == 2  # two bind placeholders
    assert "'?'" not in fix.fixed_source  # placeholders are not left quoted
