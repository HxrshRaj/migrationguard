"""MigrationGuard: a neutral behavioral verifier for AI-migrated code.

MigrationGuard doesn't migrate your code -- it proves whether the migration
you already have is safe to ship. It scans for risky patterns, proposes
fixes, and then runs the original and the fixed code side by side against a
generated battery of edge cases to prove (or disprove) that they behave the
same.
"""

__version__ = "0.1.0"
