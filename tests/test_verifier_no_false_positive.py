"""Two behaviorally identical implementations, written two different ways,
must produce zero divergences -- the verifier's false-positive control.
An audit tool that cries wolf on cosmetic code differences isn't
trustworthy even if it never misses a real bug.
"""
from __future__ import annotations

import sqlite3

from migrationguard.verifier.harness import load_function
from migrationguard.verifier.runner import verify_advanced, verify_baseline

IMPL_A = """
def find_user_by_name(conn, name):
    cur = conn.execute(
        "SELECT id, name, email, role FROM users WHERE name = ?", (name,)
    )
    return cur.fetchall()
"""

# Same behavior, different style: multi-line call, an intermediate
# variable, a trailing comment -- purely cosmetic source differences.
IMPL_B = """
def find_user_by_name(conn, name):
    query = "SELECT id, name, email, role FROM users WHERE name = ?"
    params = (name,)  # unpacked for readability
    cursor = conn.execute(query, params)
    rows = cursor.fetchall()
    return rows
"""


def _load(src: str):
    return load_function(src, "find_user_by_name", {"sqlite3": sqlite3})


def test_no_false_positive_baseline():
    a, b = _load(IMPL_A), _load(IMPL_B)
    result = verify_baseline("no-fp", a, b)
    assert result.breaking == 0
    assert result.cosmetic == 0
    assert result.identical == result.total_cases


def test_no_false_positive_advanced():
    a, b = _load(IMPL_A), _load(IMPL_B)
    result = verify_advanced("no-fp-adv", a, b, max_examples=100)
    assert result.breaking == 0
    assert result.cosmetic == 0
