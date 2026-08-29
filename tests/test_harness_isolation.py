"""Proves the harness never leaks database state between calls -- the
property the whole verifier depends on to make original-vs-fixed
comparisons fair.
"""
from __future__ import annotations

from migrationguard.demo.legacy_app import (
    count_users,
    delete_user_by_name,
    find_user_by_name,
)
from migrationguard.verifier.harness import run_once


def test_mutation_in_one_call_does_not_leak_into_the_next():
    first = run_once(delete_user_by_name, ("Alice Chen",))
    assert first.return_value == 1  # one row deleted, on a fresh db
    assert first.row_count_after == 4  # 5 seed rows minus 1

    second = run_once(delete_user_by_name, ("Alice Chen",))
    # if state leaked, Alice would already be gone and this would delete 0
    assert second.return_value == 1
    assert second.row_count_after == 4


def test_read_after_a_mutating_call_sees_a_fresh_seed_not_the_mutation():
    run_once(delete_user_by_name, ("Alice Chen",))
    behavior = run_once(find_user_by_name, ("Alice Chen",))
    assert behavior.return_value  # Alice is back: this call got its own fresh db


def test_count_users_is_stable_across_independent_calls():
    counts = [run_once(count_users, ()).return_value for _ in range(5)]
    assert counts == [5, 5, 5, 5, 5]
