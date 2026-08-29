"""Advanced fix generator: an LLM rewrite of the whole function, for
findings the baseline template can't handle -- an UNTRACEABLE query built
across multiple statements, or a recognized-but-asymmetric-quoting shape
like a LIKE-wildcard pattern. It reads the whole function, not one
expression, which is exactly the extra leverage a template rewrite
doesn't have.

Includes a self-check: the model's rewrite is parsed and its parameter
list compared against the original before being accepted. A rewrite that
silently drops or reorders a parameter is rejected, not shipped with high
confidence.
"""
from __future__ import annotations

import ast
import re

from migrationguard.llm import LLMClient
from migrationguard.models import FixCandidate, RiskFinding

_SYSTEM = (
    "You are fixing a SQL injection risk in a Python function that uses "
    "the sqlite3 module. Rewrite the function to use parameterized "
    "queries (`?` placeholders, values passed as a tuple to execute()/"
    "executemany()) instead of string interpolation, %-formatting, or "
    "concatenation. Preserve the function's exact signature (same "
    "parameter names, same order) and its behavior on well-formed input. "
    "If a wildcard character (like SQL's LIKE '%') is part of the query, "
    "bind it into the parameter value itself rather than leaving it in "
    "the SQL text. Respond with ONLY the corrected function's full source "
    "in a single python code block -- no prose before or after it."
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def generate_fix(source: str, finding: RiskFinding, llm: LLMClient) -> FixCandidate:
    function_source = _extract_function_source(source, finding.function)
    original_params = _param_names_from_source(function_source, finding.function)

    prompt = (
        f"Function to fix:\n```python\n{function_source}\n```\n\n"
        f"Flagged line ({finding.line}): {finding.code_snippet}\n"
        f"Why it's risky: {finding.risk_explanation}\n"
    )
    response = llm.complete(stage="fixgen.rewrite", system=_SYSTEM, prompt=prompt)
    code = _extract_code_block(response)
    if code is None:
        return _failure(finding, "model response did not contain a parseable code block")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return _failure(finding, f"generated code did not parse: {exc}")

    func_defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(func_defs) != 1 or func_defs[0].name != finding.function:
        return _failure(
            finding,
            "response did not define exactly one function named "
            f"{finding.function!r}",
        )

    fixed_params = [a.arg for a in func_defs[0].args.args]
    if fixed_params != original_params:
        return _failure(
            finding,
            f"self-check rejected the rewrite: parameter list changed "
            f"({original_params} -> {fixed_params})",
        )

    return FixCandidate(
        finding_id=finding.id,
        function=finding.function,
        strategy="llm",
        success=True,
        fixed_source=code,
        rationale=(
            "LLM-rewritten to use parameterized queries; self-check "
            "confirmed the parameter list is unchanged before acceptance."
        ),
        confidence=0.8,
    )


def _failure(finding: RiskFinding, reason: str) -> FixCandidate:
    return FixCandidate(
        finding_id=finding.id,
        function=finding.function,
        strategy="llm",
        success=False,
        rationale="",
        confidence=0.0,
        failure_reason=reason,
    )


def _extract_function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ast.unparse(node)
    raise ValueError(f"function {name!r} not found in source")


def _param_names_from_source(function_source: str, name: str) -> list[str]:
    tree = ast.parse(function_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [a.arg for a in node.args.args]
    return []


def _extract_code_block(response: str) -> str | None:
    match = _CODE_BLOCK_RE.search(response)
    if match:
        return match.group(1).strip()
    stripped = response.strip()
    return stripped if stripped.startswith("def ") else None
