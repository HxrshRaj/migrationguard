"""Baseline scanner: pure AST pattern matching, no LLM calls.

Walks every function in a source file, finds calls to something named
execute()/executemany(), and flags the ones whose query argument isn't a
plain string literal -- using queryexpr.analyze_query_expr to classify
*how* it's unsafe. Canned, pattern-keyed explanations and fixed confidence
values here; scanner/explain.py adds the LLM-written, context-specific
version on top in advanced mode.
"""
from __future__ import annotations

import ast

from migrationguard.models import Mode, PatternType, RiskFinding
from migrationguard.scanner.queryexpr import analyze_query_expr, resolve_query_arg

_EXECUTE_METHODS = {"execute", "executemany"}

_EXPLANATIONS: dict[PatternType, str] = {
    PatternType.FSTRING: (
        "This query is built with an f-string, so any characters in the "
        "interpolated value -- quotes, SQL keywords, wildcards -- become "
        "part of the SQL text itself instead of being treated as data. A "
        "name containing an apostrophe, or a deliberately crafted input, "
        "can change what the query actually does."
    ),
    PatternType.PERCENT_FORMAT: (
        "This query is built with Python's %-string formatting, which has "
        "the same problem as f-string interpolation: the substituted value "
        "is spliced into the query text verbatim instead of being bound as "
        "a separate parameter."
    ),
    PatternType.FORMAT_METHOD: (
        "This query is built with str.format(), so every value passed to "
        ".format() is rendered straight into the SQL text -- quotes, "
        "wildcards, and keywords in that value are parsed as SQL, not "
        "treated as data. It has the same risk as f-string interpolation."
    ),
    PatternType.CONCAT: (
        "This query is assembled with string concatenation. Every "
        "concatenated value is exposed to the SQL parser as literal text, "
        "so it can accidentally -- or deliberately -- change the query's "
        "meaning."
    ),
    PatternType.UNTRACEABLE: (
        "This call passes a query built elsewhere in the function rather "
        "than as a literal at the call site, so its safety can't be "
        "confirmed by pattern matching alone -- it needs to be traced back "
        "to how the string was assembled."
    ),
}

_BASELINE_CONFIDENCE: dict[PatternType, float] = {
    PatternType.FSTRING: 0.9,
    PatternType.PERCENT_FORMAT: 0.9,
    PatternType.FORMAT_METHOD: 0.9,
    PatternType.CONCAT: 0.9,
    PatternType.UNTRACEABLE: 0.55,
}


def scan_source(source: str, file: str) -> list[RiskFinding]:
    """Scan one Python source file's text and return every risky query
    call as a baseline RiskFinding, in source order."""
    tree = ast.parse(source, filename=file)
    findings: list[RiskFinding] = []

    for func_node in ast.walk(tree):
        if not isinstance(func_node, ast.FunctionDef):
            continue
        for call in ast.walk(func_node):
            if not isinstance(call, ast.Call):
                continue
            if not _is_execute_call(call):
                continue
            if not call.args:
                continue
            resolved_arg = resolve_query_arg(func_node, call.args[0])
            analysis = analyze_query_expr(resolved_arg)
            if analysis is None:
                continue  # already a plain literal -- safe

            snippet = ast.get_source_segment(source, call) or ast.unparse(call)
            findings.append(
                RiskFinding(
                    id=f"{func_node.name}#{call.lineno}",
                    file=file,
                    function=func_node.name,
                    line=call.lineno,
                    code_snippet=snippet,
                    pattern_type=analysis.pattern_type,
                    risk_explanation=_EXPLANATIONS[analysis.pattern_type],
                    confidence=_BASELINE_CONFIDENCE[analysis.pattern_type],
                    mode=Mode.BASELINE,
                )
            )
    return findings


def _is_execute_call(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr in _EXECUTE_METHODS
