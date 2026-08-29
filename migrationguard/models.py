"""Shared data models for the whole pipeline.

Every stage of MigrationGuard communicates through these pydantic models --
never through ad hoc dicts. That is what makes the JSONL run log and the
final report trustworthy: every object on disk has a validated shape.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Mode(str, Enum):
    BASELINE = "baseline"
    ADVANCED = "advanced"


class PatternType(str, Enum):
    FSTRING = "fstring"
    PERCENT_FORMAT = "percent_format"
    CONCAT = "concat"
    UNTRACEABLE = "untraceable"  # execute() called with a variable/expression
    # baseline's inline analyzer cannot reconstruct


class RiskFinding(BaseModel):
    id: str
    file: str
    function: str
    line: int
    code_snippet: str
    pattern_type: PatternType
    risk_explanation: str
    confidence: float = Field(ge=0.0, le=1.0)
    mode: Mode


class FixCandidate(BaseModel):
    finding_id: str
    function: str
    strategy: str  # "template" | "llm"
    success: bool
    fixed_source: Optional[str] = None
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    failure_reason: Optional[str] = None


class Severity(str, Enum):
    IDENTICAL = "identical"
    COSMETIC = "cosmetic"
    BREAKING = "breaking"


class Behavior(BaseModel):
    """A snapshot of what one call produced, captured identically for the
    original and the fixed function so the two are directly comparable.

    return_value holds whatever the function returned (a list of row
    tuples, a scalar count, None) exactly as produced -- both sides go
    through the same harness code path, so equality on this field is a
    fair comparison. row_count_after is a second, independent signal: it
    catches a divergence in a *mutation's* side effect even when the
    function's own return value looks identical (or is None on both
    sides).
    """

    return_value: Any = None
    row_count_after: Optional[int] = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None

    def effect_equal(self, other: "Behavior") -> bool:
        """True when the *observable effect* matches -- same return value,
        same row count, same exception class. Deliberately ignores
        exception message text, which alone is a cosmetic difference."""
        return (
            self.return_value == other.return_value
            and self.row_count_after == other.row_count_after
            and self.exception_type == other.exception_type
        )


class TestCaseResult(BaseModel):
    input_repr: str
    original_behavior: Behavior
    fixed_behavior: Behavior
    severity: Severity
    rationale: Optional[str] = None  # advanced-mode: LLM-written, human-facing
    # explanation of *why* this severity -- never overrides the verdict above


class VerificationResult(BaseModel):
    finding_id: str
    function: str
    mode: Mode
    total_cases: int
    identical: int
    cosmetic: int
    breaking: int
    minimal_failing_example: Optional[str] = None
    cases: list[TestCaseResult] = Field(default_factory=list)


class RunReport(BaseModel):
    """Everything one `migrationguard scan` run produced, in one object --
    this is what report/generator.py renders and what gets written to
    run.jsonl."""

    mode: Mode
    file: str
    generated_at: str
    findings: list[RiskFinding] = Field(default_factory=list)
    fixes: dict[str, FixCandidate] = Field(default_factory=dict)  # keyed by finding_id
    verifications: dict[str, VerificationResult] = Field(default_factory=dict)  # keyed by finding_id


class LLMTrajectory(BaseModel):
    """One disclosed agent call: everything needed to reconstruct what the
    LLM was asked and what it returned."""

    call_id: str
    stage: str  # "scanner.explain" | "fixgen.rewrite" | "diffengine.rationale"
    model: str
    prompt: str
    response: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    timestamp: str
