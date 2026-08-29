"""Advanced-mode explanation: the same findings the baseline scanner
produces, with an LLM-written, context-specific explanation and confidence
score in place of the canned template text.

This is deliberately additive, not a replacement pipeline: detector.py's
AST pattern matching is still what decides *whether* something is
flagged. The model only ever gets to change the explanation and the
confidence number attached to a finding that detection has already made.
"""
from __future__ import annotations

from migrationguard.llm import LLMClient
from migrationguard.models import Mode, RiskFinding

_SYSTEM = (
    "You are a security reviewer explaining a risky SQL query pattern "
    "found in a Python codebase, to a developer deciding whether to trust "
    "an automated fix. Be specific to the code shown, not generic. "
    "Respond with exactly two lines, no other text:\n"
    "EXPLANATION: <one or two sentences, plain language, specific to this code>\n"
    "CONFIDENCE: <a number between 0 and 1>"
)


def explain(finding: RiskFinding, function_source: str, llm: LLMClient) -> RiskFinding:
    prompt = (
        f"Function source:\n```python\n{function_source}\n```\n\n"
        f"Flagged line ({finding.line}): {finding.code_snippet}\n"
        f"Pattern type: {finding.pattern_type.value}\n\n"
        "Explain specifically why this is risky in this function, and how "
        "confident you are that it represents a real, exploitable risk -- "
        "for example, lower your confidence if the interpolated value "
        "looks like it traces back to a hardcoded constant rather than "
        "external input."
    )
    response = llm.complete(stage="scanner.explain", system=_SYSTEM, prompt=prompt)
    explanation, confidence = _parse(
        response,
        fallback_explanation=finding.risk_explanation,
        fallback_confidence=finding.confidence,
    )
    return finding.model_copy(
        update={
            "risk_explanation": explanation,
            "confidence": confidence,
            "mode": Mode.ADVANCED,
        }
    )


def _parse(
    response: str, *, fallback_explanation: str, fallback_confidence: float
) -> tuple[str, float]:
    explanation, confidence = fallback_explanation, fallback_confidence
    for line in response.splitlines():
        line = line.strip()
        if line.upper().startswith("EXPLANATION:"):
            value = line.split(":", 1)[1].strip()
            explanation = value or explanation
        elif line.upper().startswith("CONFIDENCE:"):
            try:
                confidence = max(0.0, min(1.0, float(line.split(":", 1)[1].strip())))
            except ValueError:
                pass
    return explanation, confidence
