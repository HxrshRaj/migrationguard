"""The curated adversarial-input list and how it's applied per parameter."""
from __future__ import annotations

import sqlite3

from migrationguard.models import Severity
from migrationguard.verifier.harness import load_function
from migrationguard.verifier.runner import verify_baseline
from migrationguard.verifier.testgen import CURATED_STRINGS, generate_baseline_cases

CORRECT_FIX = """
def find_user_by_name(conn, name):
    cur = conn.execute(
        "SELECT id, name, email, role FROM users WHERE name = ?", (name,)
    )
    return cur.fetchall()
"""


def test_curated_list_has_no_duplicates_and_covers_the_expected_axes():
    assert len(CURATED_STRINGS) == len(set(CURATED_STRINGS))
    # a few axes that must stay represented
    assert "" in CURATED_STRINGS                       # empty string
    assert "O'Brien" in CURATED_STRINGS                # single quote (flagship)
    assert "'; DROP TABLE users; --" in CURATED_STRINGS  # injection
    assert "?" in CURATED_STRINGS                      # bind-placeholder confusion
    assert "{}" in CURATED_STRINGS                     # str.format brace confusion
    assert "%s" in CURATED_STRINGS                     # %-format token confusion
    assert "''" in CURATED_STRINGS                     # SQL quote-escaping sequence
    assert any("\r\n" in s for s in CURATED_STRINGS)   # CRLF, not just \n


def test_generate_baseline_cases_shape():
    # one all-benign case, then every curated string in each position
    one = generate_baseline_cases(1)
    assert len(one) == 1 + len(CURATED_STRINGS)
    two = generate_baseline_cases(2)
    assert len(two) == 1 + 2 * len(CURATED_STRINGS)
    # first case is all-benign; no curated string leaks into it
    assert all(v == one[0][0] for v in one[0])


def test_a_guaranteed_non_matching_input_is_identical_empty_on_a_correct_fix():
    ref = load_function(CORRECT_FIX, "find_user_by_name", {"sqlite3": sqlite3})
    cand = load_function(CORRECT_FIX, "find_user_by_name", {"sqlite3": sqlite3})
    result = verify_baseline("empty-set", ref, cand)
    assert result.breaking == 0 and result.cosmetic == 0

    nobody = next(c for c in result.cases if c.input_repr == repr(("Nobody Here",)))
    assert nobody.severity == Severity.IDENTICAL
    assert nobody.original_behavior.return_value == []
    assert nobody.fixed_behavior.return_value == []
