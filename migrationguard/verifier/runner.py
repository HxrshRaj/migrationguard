"""Orchestrates one finding's verification: generate inputs, run the
original and the fixed function side by side, classify every case, and
roll it all up into a VerificationResult.

verify_baseline uses the fixed curated list. verify_advanced drives the
same harness and diff engine through Hypothesis, so a divergence Hypothesis
finds is automatically shrunk to the smallest input that still reproduces
it -- that's minimal_failing_example, and it's the single most useful line
in the report.
"""
from __future__ import annotations

from collections.abc import Callable

from hypothesis import HealthCheck, given, settings
from hypothesis import seed as hyp_seed

from migrationguard.models import Mode, Severity, TestCaseResult, VerificationResult
from migrationguard.verifier.diffengine import classify
from migrationguard.verifier.harness import run_once
from migrationguard.verifier.testgen import (
    generate_baseline_cases,
    param_names,
    strategy_for_signature,
)

# Cap how many individual case rows we keep in memory / the report / the
# run log. Summary counts (total_cases, identical/cosmetic/breaking) always
# reflect every case actually executed, capped or not.
MAX_REPORTED_CASES = 60


def verify_baseline(
    finding_id: str, original_func: Callable, fixed_func: Callable
) -> VerificationResult:
    names = param_names(original_func)
    cases = generate_baseline_cases(len(names))

    results: list[TestCaseResult] = []
    counts = {Severity.IDENTICAL: 0, Severity.COSMETIC: 0, Severity.BREAKING: 0}
    minimal_failing: str | None = None

    for args in cases:
        original_b = run_once(original_func, args)
        fixed_b = run_once(fixed_func, args)
        sev = classify(original_b, fixed_b)
        counts[sev] += 1
        if len(results) < MAX_REPORTED_CASES:
            results.append(
                TestCaseResult(
                    input_repr=repr(args),
                    original_behavior=original_b,
                    fixed_behavior=fixed_b,
                    severity=sev,
                )
            )
        if sev != Severity.IDENTICAL and minimal_failing is None:
            # first failing case found, in curated-list order -- not shrunk
            # to a minimal example the way advanced mode's is.
            minimal_failing = repr(args)

    return VerificationResult(
        finding_id=finding_id,
        function=original_func.__name__,
        mode=Mode.BASELINE,
        total_cases=len(cases),
        identical=counts[Severity.IDENTICAL],
        cosmetic=counts[Severity.COSMETIC],
        breaking=counts[Severity.BREAKING],
        minimal_failing_example=minimal_failing,
        cases=results,
    )


def verify_advanced(
    finding_id: str,
    original_func: Callable,
    fixed_func: Callable,
    *,
    max_examples: int = 200,
    seed: int = 20260830,
) -> VerificationResult:
    strategy = strategy_for_signature(original_func)
    results: list[TestCaseResult] = []
    counts = {Severity.IDENTICAL: 0, Severity.COSMETIC: 0, Severity.BREAKING: 0}
    state: dict[str, str | None] = {"minimal_failing": None}

    @settings(
        max_examples=max_examples,
        deadline=None,
        database=None,
        suppress_health_check=[
            HealthCheck.function_scoped_fixture,
            HealthCheck.too_slow,
        ],
    )
    @hyp_seed(seed)
    @given(strategy)
    def check(args: tuple) -> None:
        original_b = run_once(original_func, args)
        fixed_b = run_once(fixed_func, args)
        sev = classify(original_b, fixed_b)
        counts[sev] += 1
        if len(results) < MAX_REPORTED_CASES:
            results.append(
                TestCaseResult(
                    input_repr=repr(args),
                    original_behavior=original_b,
                    fixed_behavior=fixed_b,
                    severity=sev,
                )
            )
        if sev != Severity.IDENTICAL:
            # updated on every failing call Hypothesis makes; the *last*
            # one it makes before giving up is the fully shrunk minimum.
            state["minimal_failing"] = repr(args)
        assert sev == Severity.IDENTICAL

    try:
        check()
    except AssertionError:
        pass  # expected once Hypothesis has finished shrinking a failure

    total = sum(counts.values())
    return VerificationResult(
        finding_id=finding_id,
        function=original_func.__name__,
        mode=Mode.ADVANCED,
        total_cases=total,
        identical=counts[Severity.IDENTICAL],
        cosmetic=counts[Severity.COSMETIC],
        breaking=counts[Severity.BREAKING],
        minimal_failing_example=state["minimal_failing"],
        cases=results,
    )
