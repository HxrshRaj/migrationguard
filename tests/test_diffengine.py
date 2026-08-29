"""Table-driven tests for the verdict logic. This is the module the whole
report's credibility rests on, so every case category gets an explicit
test rather than relying on end-to-end coverage alone.
"""
from __future__ import annotations

import pytest

from migrationguard.models import Behavior, Severity
from migrationguard.verifier.diffengine import classify


def test_identical_return_values_and_row_counts():
    original = Behavior(return_value=[(1, "Alice")], row_count_after=5)
    fixed = Behavior(return_value=[(1, "Alice")], row_count_after=5)
    assert classify(original, fixed) == Severity.IDENTICAL


def test_different_return_value_is_breaking():
    original = Behavior(return_value=[(1, "Alice")], row_count_after=5)
    fixed = Behavior(return_value=[], row_count_after=5)
    assert classify(original, fixed) == Severity.BREAKING


def test_different_row_count_after_is_breaking_even_if_return_value_matches():
    # catches a mutation whose *side effect* diverges even though what the
    # call itself returned looks the same (e.g. None on both sides).
    original = Behavior(return_value=None, row_count_after=4)
    fixed = Behavior(return_value=None, row_count_after=5)
    assert classify(original, fixed) == Severity.BREAKING


def test_same_exception_type_different_message_is_cosmetic():
    original = Behavior(
        exception_type="OperationalError", exception_message="near X", row_count_after=5
    )
    fixed = Behavior(
        exception_type="OperationalError",
        exception_message="a differently worded message",
        row_count_after=5,
    )
    assert classify(original, fixed) == Severity.COSMETIC


def test_same_exception_type_and_message_is_identical():
    original = Behavior(exception_type="ValueError", exception_message="bad input", row_count_after=5)
    fixed = Behavior(exception_type="ValueError", exception_message="bad input", row_count_after=5)
    assert classify(original, fixed) == Severity.IDENTICAL


def test_different_exception_type_is_breaking():
    original = Behavior(exception_type="OperationalError", exception_message="x", row_count_after=5)
    fixed = Behavior(exception_type="ValueError", exception_message="x", row_count_after=5)
    assert classify(original, fixed) == Severity.BREAKING


def test_one_raises_other_does_not_is_breaking():
    original = Behavior(exception_type="OperationalError", exception_message="x", row_count_after=5)
    fixed = Behavior(return_value=[], row_count_after=5)
    assert classify(original, fixed) == Severity.BREAKING


@pytest.mark.parametrize(
    "original,fixed",
    [
        (Behavior(return_value=1, row_count_after=1), Behavior(return_value=1, row_count_after=1)),
        (Behavior(return_value=None, row_count_after=0), Behavior(return_value=None, row_count_after=0)),
    ],
)
def test_scalar_and_none_return_values_compare_correctly(original, fixed):
    assert classify(original, fixed) == Severity.IDENTICAL
