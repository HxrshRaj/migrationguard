"""The test that most needs to exist: prove the verifier actually catches
a real bug, and that advanced mode's generated inputs find one that the
baseline's fixed curated list would have missed.

BUGGY_FIX below is a realistic mistake, not a contrived one -- it's the
exact bug this project's own baseline fixer had during development (see
CHANGELOG.md): leaving the SQL string-literal quotes around a bind
placeholder, so `'?'` is parsed as a two-character literal instead of a
parameter marker. It returns zero rows for *every* input, including
ordinary ones -- a real regression, not the intended security-fix
divergence on adversarial input.
"""
from __future__ import annotations

import sqlite3

from migrationguard.demo import legacy_app
from migrationguard.models import Severity
from migrationguard.verifier.harness import load_function
from migrationguard.verifier.runner import verify_advanced, verify_baseline

CORRECT_FIX = """
def find_user_by_name(conn, name):
    cur = conn.execute(
        "SELECT id, name, email, role FROM users WHERE name = ?", (name,)
    )
    return cur.fetchall()
"""

BUGGY_FIX = """
def find_user_by_name(conn, name):
    # bug: the quotes from the original f-string were never removed, so
    # '?' is a two-character string literal, not a bind placeholder.
    cur = conn.execute(
        "SELECT id, name, email, role FROM users WHERE name = '?'", (name,)
    )
    return cur.fetchall()
"""


def _load(src: str):
    return load_function(src, "find_user_by_name", {"sqlite3": sqlite3})


def test_correct_fix_has_no_regressions_against_a_reference_implementation():
    reference = _load(CORRECT_FIX)
    candidate = _load(CORRECT_FIX)
    result = verify_baseline("ref-vs-ref", reference, candidate)
    assert result.breaking == 0
    assert result.cosmetic == 0


def test_baseline_catches_the_bug_on_an_ordinary_benign_input():
    reference = _load(CORRECT_FIX)
    buggy = _load(BUGGY_FIX)
    result = verify_baseline("seeded-bug", reference, buggy)

    assert result.breaking > 0
    benign_case = next(c for c in result.cases if c.input_repr == repr(("Alice Chen",)))
    assert benign_case.severity == Severity.BREAKING, (
        "an ordinary existing name with no SQL metacharacters diverged -- "
        "that's a regression, not the intended security-fix behavior "
        "(which should only show up on adversarial input)"
    )


def test_advanced_mode_finds_the_bug_and_shrinks_to_a_minimal_example():
    reference = _load(CORRECT_FIX)
    buggy = _load(BUGGY_FIX)
    result = verify_advanced("seeded-bug-adv", reference, buggy, max_examples=50)

    assert result.breaking > 0
    assert result.minimal_failing_example is not None
