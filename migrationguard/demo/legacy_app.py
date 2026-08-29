"""The 'legacy' codebase MigrationGuard scans in this demo.

This is a small UserDirectory service, migrated (badly, in most cases) to
use f-strings and string concatenation for its SQL instead of parameterized
queries -- exactly the pattern class MigrationGuard targets. It is used
consistently across the test suite, the README example, the reproduction
guide, and the demo video, so that every artifact in the submission is
talking about the same code.

Two functions at the bottom (find_user_by_id, count_users) are already safe
and are negative controls: the scanner must never flag them.
"""
from __future__ import annotations

import sqlite3


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            age INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()


def seed_db(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO users (id, name, email, role, age) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


# --------------------------------------------------------------------------
# RISKY: unsafe string-formatted queries. Each of these is what a naive
# AI migration ("looks right, passes the obvious test") leaves behind.
# --------------------------------------------------------------------------


def find_user_by_name(conn: sqlite3.Connection, name: str):
    """Pattern: f-string."""
    query = f"SELECT id, name, email, role FROM users WHERE name = '{name}'"
    cur = conn.execute(query)
    return cur.fetchall()


def find_users_by_role(conn: sqlite3.Connection, role: str):
    """Pattern: %-format."""
    query = "SELECT id, name, email, role FROM users WHERE role = '%s'" % role
    cur = conn.execute(query)
    return cur.fetchall()


def search_users_by_email_domain(conn: sqlite3.Connection, domain: str):
    """Pattern: string concatenation."""
    query = "SELECT id, name, email FROM users WHERE email LIKE '%" + domain + "'"
    cur = conn.execute(query)
    return cur.fetchall()


def delete_user_by_name(conn: sqlite3.Connection, name: str) -> int:
    """Pattern: f-string, and a mutation -- an injected quote here doesn't
    just leak rows, it can delete the wrong ones."""
    query = f"DELETE FROM users WHERE name = '{name}'"
    before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.execute(query)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return before - after


def update_user_role(conn: sqlite3.Connection, name: str, new_role: str) -> None:
    """Pattern: f-string, two interpolated values."""
    query = f"UPDATE users SET role = '{new_role}' WHERE name = '{name}'"
    conn.execute(query)
    conn.commit()


def get_user_by_id_unsafe(conn: sqlite3.Connection, user_id) -> list:
    """Pattern: concatenation with str(). Looks 'safe' because the field is
    numeric, but user_id can arrive as a string from a web form -- and then
    it's exactly as unsafe as the string patterns above."""
    query = "SELECT id, name, email, role FROM users WHERE id = " + str(user_id)
    cur = conn.execute(query)
    return cur.fetchall()


def list_active_users_over_age(conn: sqlite3.Connection, min_age: int, role: str):
    """Pattern: query built across multiple statements before it reaches
    execute(). A baseline scanner that only inspects the literal argument
    expression at the call site can flag this call (it isn't a plain string
    literal) but cannot reconstruct the query shape to auto-fix it -- that
    needs an LLM reading the whole function. This is the flagship example
    for why the advanced fixer exists."""
    query = f"SELECT id, name, email FROM users WHERE age > {min_age}"
    query += f" AND role = '{role}'"
    cur = conn.execute(query)
    return cur.fetchall()


# --------------------------------------------------------------------------
# CLEAN: negative controls. Already parameterized -- the scanner must
# report zero findings on these.
# --------------------------------------------------------------------------


def find_user_by_id(conn: sqlite3.Connection, user_id: int):
    cur = conn.execute(
        "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
    )
    return cur.fetchall()


def count_users(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM users")
    return cur.fetchone()[0]
