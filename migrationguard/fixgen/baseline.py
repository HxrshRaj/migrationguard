"""Baseline fix generator: deterministic AST rewrite, no LLM involved.

Handles exactly the three shapes queryexpr can fully parameterize
(f-string, %-format, concatenation) by rewriting the execute()/
executemany() call to `execute(sql_with_placeholders, (params...))` and
deleting the now-unused query-building assignment. Returns success=False
with a stated reason on anything it can't confidently rewrite -- notably
PatternType.UNTRACEABLE, which is exactly what the advanced fixer exists
for.
"""
from __future__ import annotations

import ast
import copy

from migrationguard.models import FixCandidate, RiskFinding
from migrationguard.scanner.queryexpr import (
    analyze_query_expr,
    find_single_assign,
    resolve_query_arg,
)

_EXECUTE_METHODS = {"execute", "executemany"}


def generate_fix(source: str, finding: RiskFinding) -> FixCandidate:
    tree = ast.parse(source)
    func_node = _find_function(tree, finding.function)
    if func_node is None:
        return _failure(finding, "function not found in source")

    call_node = _find_execute_call_at_line(func_node, finding.line)
    if call_node is None or not call_node.args:
        return _failure(finding, "could not relocate the flagged call in the AST")

    resolved_arg = resolve_query_arg(func_node, call_node.args[0])
    analysis = analyze_query_expr(resolved_arg)
    if analysis is None or analysis.parameterized_sql is None:
        return _failure(finding, _unfixable_reason(analysis))

    fixed_func = copy.deepcopy(func_node)
    fixed_call = _find_execute_call_at_line(fixed_func, finding.line)
    assert fixed_call is not None  # a deepcopy of func_node, where we just found it
    assign_stmt = find_single_assign(fixed_func, call_node.args[0])
    if assign_stmt is not None:
        fixed_func.body.remove(assign_stmt)

    fixed_call.args = [
        ast.Constant(value=analysis.parameterized_sql),
        ast.Tuple(
            elts=[copy.deepcopy(p) for p in analysis.param_exprs], ctx=ast.Load()
        ),
    ]

    ast.fix_missing_locations(fixed_func)
    fixed_source = ast.unparse(fixed_func)

    return FixCandidate(
        finding_id=finding.id,
        function=finding.function,
        strategy="template",
        success=True,
        fixed_source=fixed_source,
        rationale=(
            f"Rewrote the {analysis.pattern_type.value} query to a "
            f"parameterized call: {len(analysis.param_exprs)} interpolated "
            f"value(s) are now bound as separate parameters instead of "
            f"spliced into the SQL text."
        ),
        confidence=0.95,
    )


def _unfixable_reason(analysis) -> str:
    from migrationguard.models import PatternType

    if analysis is None or analysis.pattern_type == PatternType.UNTRACEABLE:
        return (
            "the query is built across more than one assignment (or "
            "reassigned) before it reaches execute(); a template rewrite "
            "can't safely reconstruct it -- this needs the advanced (LLM) "
            "fixer, which reads the whole function instead of one "
            "expression."
        )
    return (
        "a SQL string-literal quote sits on only one side of an "
        "interpolated value (for example, a LIKE wildcard baked into the "
        "query text: `'%' + value`), so stripping the quote to bind a "
        "clean parameter isn't safe to do mechanically -- this needs the "
        "advanced (LLM) fixer, which can restructure the query (e.g. bind "
        "the wildcard into the parameter value itself: `LIKE ?` with "
        "param `f'%{value}'`)."
    )


def _failure(finding: RiskFinding, reason: str) -> FixCandidate:
    return FixCandidate(
        finding_id=finding.id,
        function=finding.function,
        strategy="template",
        success=False,
        rationale="",
        confidence=0.0,
        failure_reason=reason,
    )


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _find_execute_call_at_line(func_node: ast.FunctionDef, line: int) -> ast.Call | None:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and node.lineno == line:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _EXECUTE_METHODS:
                return node
    return None
