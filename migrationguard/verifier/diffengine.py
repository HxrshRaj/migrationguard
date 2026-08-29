"""The verdict logic. Deliberately pure and deterministic -- no LLM call
anywhere in this file, in either mode.

This is the one module in the whole pipeline whose output has to be
trustworthy without qualification, because it's the thing every claim in
the final report reduces to ("identical across N cases" / "breaking here").
Handing severity classification to a model would make the audit-ready
report only as trustworthy as the model's mood that day -- exactly the
kind of unverifiable confidence MigrationGuard exists to replace. Advanced
mode adds a natural-language *rationale* for a verdict (see
verifier/rationale.py); it never gets a vote on the verdict itself.
"""
from __future__ import annotations

from migrationguard.models import Behavior, Severity


def classify(original: Behavior, fixed: Behavior) -> Severity:
    """BREAKING: the observable effect differs -- different return value,
    different row count, or a different exception class. COSMETIC: both
    sides raised the *same* exception type with the *same* net effect, but
    the exception's message text differs. IDENTICAL: everything matches,
    including message text."""
    if not original.effect_equal(fixed):
        return Severity.BREAKING
    if (
        original.exception_type is not None
        and original.exception_message != fixed.exception_message
    ):
        return Severity.COSMETIC
    return Severity.IDENTICAL
