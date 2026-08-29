"""Advanced-mode addition to a verification case: an LLM-written,
human-facing sentence on *why* a divergence matters.

This never touches the severity verdict -- diffengine.classify() has
already decided that deterministically by the time this runs. All this
adds is narrative: does this divergence look like the security fix
correctly changing behavior on an adversarial input (expected), or a
possible regression on an input that should have behaved identically
(needs review)? See report/generator.py for the non-LLM heuristic version
of this same question, used in baseline mode.
"""
from __future__ import annotations

from migrationguard.llm import LLMClient
from migrationguard.models import Severity, TestCaseResult

_SYSTEM = (
    "You annotate a detected behavioral divergence between the original "
    "(unsafe) and fixed (parameterized) version of a SQL query function, "
    "for an audit report. In one direct sentence, say whether this looks "
    "like the security fix correctly changing behavior on an adversarial "
    "input, or a possible regression on an input that should have stayed "
    "identical. The severity verdict is already decided -- do not restate "
    "or second-guess it, just explain it."
)


def annotate(case: TestCaseResult, llm: LLMClient) -> TestCaseResult:
    if case.severity == Severity.IDENTICAL:
        return case
    prompt = (
        f"Input: {case.input_repr}\n"
        f"Original behavior: {case.original_behavior.model_dump_json()}\n"
        f"Fixed behavior: {case.fixed_behavior.model_dump_json()}\n"
        f"Severity verdict (already decided): {case.severity.value}\n"
    )
    rationale = llm.complete(stage="diffengine.rationale", system=_SYSTEM, prompt=prompt)
    return case.model_copy(update={"rationale": rationale.strip()})
