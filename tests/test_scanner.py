"""Scanner correctness against the demo codebase: every risky function
flagged with the right pattern type, and -- just as important -- zero
false positives on the two functions that are already safe."""
from __future__ import annotations

import inspect

from migrationguard.demo import legacy_app
from migrationguard.models import PatternType
from migrationguard.scanner.detector import scan_source

SOURCE = inspect.getsource(legacy_app)
FILE = inspect.getfile(legacy_app)

EXPECTED_PATTERNS = {
    "find_user_by_name": PatternType.FSTRING,
    "find_users_by_role": PatternType.PERCENT_FORMAT,
    "search_users_by_email_domain": PatternType.CONCAT,
    "delete_user_by_name": PatternType.FSTRING,
    "update_user_role": PatternType.FSTRING,
    "get_user_by_id_unsafe": PatternType.CONCAT,
    "list_active_users_over_age": PatternType.UNTRACEABLE,
}

SAFE_FUNCTIONS = {"find_user_by_id", "count_users", "init_db", "seed_db"}


def test_flags_exactly_the_expected_functions_and_patterns():
    findings = scan_source(SOURCE, FILE)
    by_function = {f.function: f for f in findings}

    assert set(by_function) == set(EXPECTED_PATTERNS)
    for function, expected_pattern in EXPECTED_PATTERNS.items():
        assert by_function[function].pattern_type == expected_pattern


def test_no_false_positives_on_already_safe_functions():
    findings = scan_source(SOURCE, FILE)
    flagged_functions = {f.function for f in findings}
    assert flagged_functions.isdisjoint(SAFE_FUNCTIONS)


def test_every_finding_has_an_explanation_and_confidence():
    findings = scan_source(SOURCE, FILE)
    assert findings, "expected at least one finding on the demo app"
    for finding in findings:
        assert finding.risk_explanation
        assert 0.0 <= finding.confidence <= 1.0
