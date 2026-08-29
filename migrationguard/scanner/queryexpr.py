"""AST analysis of the query-building expression passed to execute()/executemany().

Shared by the scanner (to detect and classify risky patterns) and the
baseline fix generator (to mechanically rewrite recognizable shapes into
parameterized form). Keeping this logic in one place is what keeps the
scanner's classification and the fixer's capability honest with each
other -- if the analyzer can't reconstruct a shape, both stages agree it
can't, instead of the fixer silently failing on something the scanner
called simple.
"""
from __future__ import annotations

import ast
import re
import string
from dataclasses import dataclass, field

from migrationguard.models import PatternType

# A ("lit", text) or ("param", expr) piece of a query being reconstructed.
Segment = tuple[str, object]


@dataclass
class QueryAnalysis:
    pattern_type: PatternType
    # None means "recognized as risky, but not safely template-fixable" --
    # either UNTRACEABLE, or a recognized shape where a SQL string-literal
    # quote sits on only one side of an interpolated value (see
    # _segments_to_analysis below).
    parameterized_sql: str | None
    param_exprs: list[ast.expr] = field(default_factory=list)  # in bind order


def _collect_name_assignments(func_node: ast.FunctionDef, name: str) -> list[ast.stmt]:
    assigns: list[ast.stmt] = []
    for stmt in ast.walk(func_node):
        if stmt is func_node:
            continue
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in stmt.targets
        ):
            assigns.append(stmt)
        elif (
            isinstance(stmt, ast.AugAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == name
        ):
            assigns.append(stmt)
    return assigns


def resolve_query_arg(func_node: ast.FunctionDef, arg: ast.expr) -> ast.expr:
    """If arg is a bare Name (the common, realistic shape -- build the
    query into a variable, then call execute(query)), trace it back to a
    single simple assignment earlier in the same function body and return
    that assignment's value expression instead.

    Returns arg unchanged when it isn't a Name, or when the variable is
    reassigned / built incrementally (an Assign plus one or more AugAssign)
    -- that shape is exactly what stays UNTRACEABLE for the baseline: real,
    but not something an inline AST rewrite can safely reconstruct.
    """
    if not isinstance(arg, ast.Name):
        return arg
    assigns = _collect_name_assignments(func_node, arg.id)
    if len(assigns) == 1 and isinstance(assigns[0], ast.Assign):
        return assigns[0].value
    return arg


def find_single_assign(func_node: ast.FunctionDef, arg: ast.expr) -> ast.Assign | None:
    """The Assign statement resolve_query_arg used to resolve `arg`, if
    any -- so the fixer can remove that now-unused line once its right-hand
    side has been inlined into the parameterized execute() call."""
    if not isinstance(arg, ast.Name):
        return None
    assigns = _collect_name_assignments(func_node, arg.id)
    if len(assigns) == 1 and isinstance(assigns[0], ast.Assign):
        return assigns[0]
    return None


def analyze_query_expr(expr: ast.expr) -> QueryAnalysis | None:
    """None means expr is already safe (a plain string literal) -- nothing
    to flag. Otherwise: a recognized, mechanically-fixable shape, or
    UNTRACEABLE -- risky, but this analyzer can't reconstruct it (e.g.
    execute() called with a bare variable referring to a query assembled
    earlier in the function)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return None

    if isinstance(expr, ast.JoinedStr):
        return _analyze_fstring(expr)

    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Mod):
        result = _analyze_percent(expr)
        if result is not None:
            return result

    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        result = _analyze_concat(expr)
        if result is not None:
            return result

    if isinstance(expr, ast.Call):
        result = _analyze_format(expr)
        if result is not None:
            return result

    return QueryAnalysis(PatternType.UNTRACEABLE, None, [])


def _segments_to_analysis(segments: list[Segment], pattern_type: PatternType) -> QueryAnalysis:
    """Turn (lit | param) segments into a QueryAnalysis, stripping the SQL
    string-literal quotes that typically surround an interpolated value
    (`'{name}'` -> bind `name` directly, no quotes) -- because a bind
    placeholder *inside* a quoted SQL string literal isn't a placeholder at
    all, it's just the two characters `'` and `?`.

    Only strips when a `'` sits on *both* sides of a param -- a clean,
    symmetric wrap. When a quote sits on only one side (e.g. a LIKE pattern
    built as `'%' + value`, where the wildcard is baked into the SQL text
    instead of the bound value), the shape is real but not one this
    analyzer can safely rewrite -- parameterized_sql comes back None, and
    the caller reports it as recognized-but-unfixable rather than guessing.
    """
    working = list(segments)
    ambiguous = False
    for i, (kind, _value) in enumerate(working):
        if kind != "param":
            continue
        prev_quoted = (
            i - 1 >= 0 and working[i - 1][0] == "lit" and working[i - 1][1].endswith("'")
        )
        next_quoted = (
            i + 1 < len(working)
            and working[i + 1][0] == "lit"
            and working[i + 1][1].startswith("'")
        )
        if prev_quoted and next_quoted:
            working[i - 1] = ("lit", working[i - 1][1][:-1])
            working[i + 1] = ("lit", working[i + 1][1][1:])
        elif prev_quoted != next_quoted:
            ambiguous = True

    params = [value for kind, value in working if kind == "param"]
    if ambiguous:
        return QueryAnalysis(pattern_type, None, params)

    sql = "".join(value if kind == "lit" else "?" for kind, value in working)
    return QueryAnalysis(pattern_type, sql, params)


def _analyze_fstring(expr: ast.JoinedStr) -> QueryAnalysis:
    segments: list[Segment] = []
    for value in expr.values:
        if isinstance(value, ast.Constant):
            segments.append(("lit", str(value.value)))
        elif isinstance(value, ast.FormattedValue):
            segments.append(("param", value.value))
        else:  # pragma: no cover - defensive, no such node type in practice
            return QueryAnalysis(PatternType.UNTRACEABLE, None, [])
    return _segments_to_analysis(segments, PatternType.FSTRING)


_PLACEHOLDER_RE = re.compile(r"%s|%d|%r")


def _analyze_percent(expr: ast.BinOp) -> QueryAnalysis | None:
    left, right = expr.left, expr.right
    if not (isinstance(left, ast.Constant) and isinstance(left.value, str)):
        return None
    template = left.value
    matches = list(_PLACEHOLDER_RE.finditer(template))
    if not matches:
        return None

    params_src = list(right.elts) if isinstance(right, ast.Tuple) else [right]
    if len(params_src) != len(matches):
        return None  # shape we can't confidently line up -- fall through

    segments: list[Segment] = []
    pos = 0
    for match, param in zip(matches, params_src):
        if match.start() > pos:
            segments.append(("lit", template[pos : match.start()]))
        segments.append(("param", param))
        pos = match.end()
    if pos < len(template):
        segments.append(("lit", template[pos:]))
    return _segments_to_analysis(segments, PatternType.PERCENT_FORMAT)


def _flatten_add_chain(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _flatten_add_chain(expr.left) + _flatten_add_chain(expr.right)
    return [expr]


def _analyze_concat(expr: ast.BinOp) -> QueryAnalysis | None:
    operands = _flatten_add_chain(expr)
    segments: list[Segment] = []
    for operand in operands:
        if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
            segments.append(("lit", operand.value))
        elif (
            isinstance(operand, ast.Call)
            and isinstance(operand.func, ast.Name)
            and operand.func.id == "str"
            and len(operand.args) == 1
        ):
            # str(x) used only to coerce for concatenation -- the real
            # parameter is the underlying value; sqlite binds it correctly.
            segments.append(("param", operand.args[0]))
        else:
            segments.append(("param", operand))
    return _segments_to_analysis(segments, PatternType.CONCAT)


_FORMATTER = string.Formatter()


def _analyze_format(expr: ast.Call) -> QueryAnalysis | None:
    """`"... {} ... {name} ...".format(a, name=b)`. Returns None if this
    isn't a `.format()` call on a string literal at all (so the caller
    falls through to UNTRACEABLE); returns a QueryAnalysis with
    parameterized_sql=None for a recognized-but-not-mechanically-fixable
    shape (a format spec / conversion that changes the value, mixed auto
    and manual field numbering, attribute/index access in a field, an
    arg-count mismatch, or a `**kwargs` splat)."""
    func = expr.func
    if not (isinstance(func, ast.Attribute) and func.attr == "format"):
        return None
    template_node = func.value
    if not (isinstance(template_node, ast.Constant) and isinstance(template_node.value, str)):
        return None  # e.g. `query_template.format(...)` -- not a literal we can read
    template = template_node.value

    if any(kw.arg is None for kw in expr.keywords):  # `.format(**mapping)`
        return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
    pos_args = list(expr.args)
    if any(isinstance(a, ast.Starred) for a in pos_args):  # `.format(*args)`
        return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
    kw_args = {kw.arg: kw.value for kw in expr.keywords}

    try:
        parsed = list(_FORMATTER.parse(template))
    except ValueError:
        return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])

    segments: list[Segment] = []
    auto_index = 0
    seen_auto = seen_manual = False
    for literal_text, field_name, format_spec, conversion in parsed:
        if literal_text:
            segments.append(("lit", literal_text))
        if field_name is None:
            continue
        if format_spec or conversion:
            # `{:>10}` / `{!r}` reshape the value before it lands in the
            # SQL text -- binding the raw value wouldn't be equivalent.
            return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
        if "." in field_name or "[" in field_name:  # `{0.attr}` / `{0[1]}`
            return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
        if field_name == "":
            seen_auto = True
            if auto_index >= len(pos_args):
                return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
            segments.append(("param", pos_args[auto_index]))
            auto_index += 1
        elif field_name.isdigit():
            seen_manual = True
            idx = int(field_name)
            if idx >= len(pos_args):
                return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
            segments.append(("param", pos_args[idx]))
        else:
            if field_name not in kw_args:
                return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
            segments.append(("param", kw_args[field_name]))
    if seen_auto and seen_manual:  # "{} {0}".format(...) is a ValueError at runtime
        return QueryAnalysis(PatternType.FORMAT_METHOD, None, [])
    return _segments_to_analysis(segments, PatternType.FORMAT_METHOD)
