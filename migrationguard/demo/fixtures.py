"""Deterministic seed data shared by every test case the verifier runs.

The seed set deliberately includes a name with an apostrophe (O'Brien) --
that single row is what turns "looks migrated" into a demonstrable bug: a
string-formatted WHERE clause breaks or misbehaves on it, a parameterized
one doesn't.
"""
from __future__ import annotations

import sqlite3

from migrationguard.demo.legacy_app import init_db, seed_db

SEED_ROWS: list[tuple] = [
    (1, "Alice Chen", "alice@example.com", "admin", 34),
    (2, "Bob Singh", "bob@example.com", "member", 22),
    (3, "O'Brien", "obrien@example.com", "member", 41),
    (4, "Priya Nair", "priya@corp.example.com", "admin", 29),
    (5, "Dana White", "dana@example.com", "member", 19),
]


def make_db() -> sqlite3.Connection:
    """A fresh, identically-seeded in-memory database. Called once per
    (function, test case, side) so no state ever leaks between calls."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    seed_db(conn, SEED_ROWS)
    return conn
