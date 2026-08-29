"""Runs the original and the fixed function side by side against
independent, identically-seeded databases, and captures a Behavior
snapshot from each so the diff engine can compare them fairly.

Isolation is the whole point of this module: every single call -- original
or fixed, any test case -- gets its own fresh in-memory sqlite3 connection
seeded from the same fixture data. Nothing here is shared or reused across
calls, so no test case can ever see state left behind by another.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from migrationguard.demo.fixtures import make_db
from migrationguard.models import Behavior


def load_function(source: str, function_name: str, module_globals: dict) -> Callable:
    """exec()s `source` (expected to be one function definition, as
    produced by fixgen) in a fresh namespace seeded with module_globals
    (e.g. {"sqlite3": sqlite3}), and returns the resulting callable."""
    namespace = dict(module_globals)
    exec(compile(source, filename=f"<fixed:{function_name}>", mode="exec"), namespace)
    if function_name not in namespace:
        raise ValueError(
            f"expected fixed source to define {function_name!r}, but it "
            f"defined: {sorted(k for k in namespace if not k.startswith('__'))}"
        )
    return namespace[function_name]


def run_once(func: Callable, args: tuple) -> Behavior:
    """Call func(conn, *args) against a fresh seeded database and capture
    exactly what happened -- return value, resulting row count, or the
    exception it raised. Never lets an exception escape: that's the whole
    point, a divergence in *what breaks* is as important as a divergence
    in return value."""
    conn = make_db()
    try:
        result = func(conn, *args)
        row_count_after = _safe_row_count(conn)
        return Behavior(return_value=result, row_count_after=row_count_after)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: we diff *any* divergence
        return Behavior(
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            row_count_after=_safe_row_count(conn),
        )
    finally:
        conn.close()


def _safe_row_count(conn: sqlite3.Connection) -> int | None:
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    except Exception:  # noqa: BLE001 - the table itself may be gone in a pathological case
        return None
