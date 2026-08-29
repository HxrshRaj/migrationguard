"""Tests for the shared AST query-expression analyzer -- the logic both
the scanner and the baseline fixer depend on agreeing about."""
from __future__ import annotations

import ast

from migrationguard.models import PatternType
from migrationguard.scanner.queryexpr import analyze_query_expr, resolve_query_arg


def _expr(src: str) -> ast.expr:
    return ast.parse(src, mode="eval").body


def test_plain_string_literal_is_safe():
    assert analyze_query_expr(_expr("'SELECT * FROM users'")) is None


def test_fstring_with_quoted_placeholder_strips_quotes():
    analysis = analyze_query_expr(_expr("f\"WHERE name = '{name}'\""))
    assert analysis.pattern_type == PatternType.FSTRING
    assert analysis.parameterized_sql == "WHERE name = ?"
    assert len(analysis.param_exprs) == 1


def test_percent_format_with_quoted_placeholder():
    analysis = analyze_query_expr(_expr("\"WHERE role = '%s'\" % role"))
    assert analysis.pattern_type == PatternType.PERCENT_FORMAT
    assert analysis.parameterized_sql == "WHERE role = ?"


def test_percent_format_with_tuple_args():
    analysis = analyze_query_expr(_expr("\"WHERE a = '%s' AND b = '%s'\" % (x, y)"))
    assert analysis.parameterized_sql == "WHERE a = ? AND b = ?"
    assert len(analysis.param_exprs) == 2


def test_concat_with_unquoted_numeric_id_is_fixable():
    analysis = analyze_query_expr(_expr("'WHERE id = ' + str(user_id)"))
    assert analysis.pattern_type == PatternType.CONCAT
    assert analysis.parameterized_sql == "WHERE id = ?"


def test_concat_with_asymmetric_quote_is_recognized_but_unfixable():
    # a LIKE wildcard baked into the SQL text -- a quote sits on only one
    # side of the parameter, so stripping it mechanically isn't safe.
    analysis = analyze_query_expr(_expr("\"WHERE email LIKE '%\" + domain + \"'\""))
    assert analysis.pattern_type == PatternType.CONCAT
    assert analysis.parameterized_sql is None
    assert len(analysis.param_exprs) == 1  # still identified -- just not auto-fixable


def test_call_result_is_untraceable():
    analysis = analyze_query_expr(_expr("build_query(name)"))
    assert analysis.pattern_type == PatternType.UNTRACEABLE
    assert analysis.parameterized_sql is None


def test_resolve_single_assignment():
    func = ast.parse(
        "def f(conn, name):\n"
        "    query = f\"SELECT * FROM users WHERE name = '{name}'\"\n"
        "    conn.execute(query)\n"
    ).body[0]
    call = func.body[1].value
    resolved = resolve_query_arg(func, call.args[0])
    assert isinstance(resolved, ast.JoinedStr)


def test_resolve_leaves_multi_statement_build_unresolved():
    func = ast.parse(
        "def f(conn, min_age, role):\n"
        "    query = f'WHERE age > {min_age}'\n"
        "    query += f' AND role = {role}'\n"
        "    conn.execute(query)\n"
    ).body[0]
    call = func.body[2].value
    resolved = resolve_query_arg(func, call.args[0])
    # unresolved: still the bare Name, because it's built across two
    # statements (Assign + AugAssign), which resolve_query_arg refuses to
    # collapse into one expression.
    assert isinstance(resolved, ast.Name)
    assert resolved.id == "query"
