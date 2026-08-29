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


# --- str.format() ---------------------------------------------------------


def test_format_method_with_quoted_placeholder_strips_quotes():
    analysis = analyze_query_expr(_expr("\"WHERE name = '{}'\".format(name)"))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql == "WHERE name = ?"
    assert len(analysis.param_exprs) == 1


def test_format_method_auto_numbered_two_fields():
    analysis = analyze_query_expr(
        _expr("\"WHERE a = '{}' AND b = '{}'\".format(x, y)")
    )
    assert analysis.parameterized_sql == "WHERE a = ? AND b = ?"
    assert [p.id for p in analysis.param_exprs] == ["x", "y"]


def test_format_method_manual_numbering_binds_in_template_order():
    # fields reference args out of order; binds must follow the order the
    # placeholders appear in the SQL text, not the .format() arg order.
    analysis = analyze_query_expr(
        _expr("\"WHERE a = '{1}' AND b = '{0}'\".format(x, y)")
    )
    assert analysis.parameterized_sql == "WHERE a = ? AND b = ?"
    assert [p.id for p in analysis.param_exprs] == ["y", "x"]


def test_format_method_keyword_field():
    analysis = analyze_query_expr(
        _expr("\"WHERE role = '{role}'\".format(role=r)")
    )
    assert analysis.parameterized_sql == "WHERE role = ?"
    assert [p.id for p in analysis.param_exprs] == ["r"]


def test_format_method_with_format_spec_is_recognized_but_unfixable():
    analysis = analyze_query_expr(_expr('"WHERE id = {0:d}".format(uid)'))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql is None


def test_format_method_with_conversion_is_recognized_but_unfixable():
    analysis = analyze_query_expr(_expr('"WHERE x = {!r}".format(v)'))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql is None


def test_format_method_mixed_auto_and_manual_numbering_is_unfixable():
    analysis = analyze_query_expr(_expr('"{} {0}".format(a, b)'))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql is None


def test_format_method_asymmetric_quote_is_unfixable():
    # LIKE wildcard baked into the text -- quote on only one side, same as
    # the concat case, so the analyzer refuses to strip it mechanically.
    analysis = analyze_query_expr(_expr("\"WHERE email LIKE '%{}'\".format(domain)"))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql is None


def test_format_method_arg_count_mismatch_is_unfixable():
    analysis = analyze_query_expr(_expr('"{} {}".format(a)'))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql is None


def test_format_method_attribute_access_in_field_is_unfixable():
    analysis = analyze_query_expr(_expr('"WHERE id = {0.pk}".format(obj)'))
    assert analysis.pattern_type == PatternType.FORMAT_METHOD
    assert analysis.parameterized_sql is None


def test_format_on_non_literal_template_is_untraceable():
    analysis = analyze_query_expr(_expr("query_template.format(name)"))
    assert analysis.pattern_type == PatternType.UNTRACEABLE
    assert analysis.parameterized_sql is None


def test_triple_quoted_fstring_is_analyzed_like_any_other_fstring():
    src = 'f"""\n    SELECT * FROM users\n    WHERE name = \'{name}\'\n    """'
    analysis = analyze_query_expr(_expr(src))
    assert analysis.pattern_type == PatternType.FSTRING
    assert analysis.parameterized_sql is not None
    assert "?" in analysis.parameterized_sql
    assert "{name}" not in analysis.parameterized_sql
    assert len(analysis.param_exprs) == 1


def test_implicitly_concatenated_fstring_parts_are_one_joinedstr():
    # Python fuses adjacent string literals at parse time; a query split
    # across lines this way is still a single f-string node.
    src = "(\n  f\"SELECT * FROM users \"\n  f\"WHERE name = '{name}' AND role = '{role}'\"\n)"
    analysis = analyze_query_expr(_expr(src))
    assert analysis.pattern_type == PatternType.FSTRING
    assert analysis.parameterized_sql == "SELECT * FROM users WHERE name = ? AND role = ?"
    assert len(analysis.param_exprs) == 2


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
