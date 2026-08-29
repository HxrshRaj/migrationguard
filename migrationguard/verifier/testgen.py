"""Test input generation: baseline curated list vs advanced Hypothesis
strategies built from the function's own signature.

Both target the parameters of the function *besides* the leading `conn` --
those are what a caller actually varies.
"""
from __future__ import annotations

import inspect
import typing
from collections.abc import Callable

from hypothesis import strategies as st

# ~20 fixed adversarial strings, hand-picked for this pattern class: quotes
# (the flagship case), SQL wildcards, injection attempts, unicode, length,
# and format confusion. This is the entire baseline test budget -- no
# generation, no shrinking, just this list, applied once per parameter
# position.
CURATED_STRINGS: list[str] = [
    "",
    "Alice Chen",  # exact existing-row match
    "Nobody Here",  # guaranteed non-match
    "O'Brien",  # single quote -- the flagship divergence
    'Say "hi"',  # double quote
    "50% off",  # SQL wildcard %
    "under_score",  # SQL wildcard _
    "'; DROP TABLE users; --",  # injection attempt
    "' OR '1'='1",  # classic injection
    "Müller",  # unicode
    "emoji \U0001F600",  # unicode outside the BMP
    "line\nbreak",  # embedded newline
    "tab\ttab",  # embedded tab
    "   ",  # whitespace-only
    "a" * 500,  # very long string
    "123",  # numeric-looking string
    "true",  # boolean-looking string
    "-- comment",  # SQL line-comment marker
    "/* block */",  # SQL block-comment marker
    "back\\slash",  # backslash
    "''",  # doubled single quote -- SQL's own quote-escaping sequence
    "?",  # a literal question mark -- bind-placeholder confusion
    "{}",  # str.format field braces -- format-confusion
    "%s",  # a %-format token -- format-confusion
    "NULL",  # the SQL NULL keyword as a string
    "5 > 3 OR 1=1",  # a boolean expression, no quote/keyword metacharacters
    "line1\r\nline2",  # CRLF, not just \n
]

BENIGN_DEFAULT = "safe_value"


def param_names(func: Callable) -> list[str]:
    """Every parameter of func except the leading `conn`."""
    sig = inspect.signature(func)
    return [name for name in sig.parameters if name != "conn"]


def param_types(func: Callable) -> dict[str, type]:
    """Resolves via typing.get_type_hints rather than reading
    param.annotation directly -- the demo module uses
    `from __future__ import annotations` (PEP 563), which turns every
    annotation into an unevaluated string ('int', not the int class), so a
    raw `param.annotation is int` check silently and permanently fails.
    get_type_hints() evaluates those strings back into real types using
    the function's own __globals__. Untyped parameters (get_user_by_id_
    unsafe's user_id, deliberately) fall back to str -- the case that
    matters, since that function is unsafe precisely because a caller can
    hand it a string."""
    sig = inspect.signature(func)
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # noqa: BLE001 - fall back to str for anything unresolvable
        hints = {}
    return {name: hints.get(name, str) for name in sig.parameters if name != "conn"}


def generate_baseline_cases(count: int) -> list[tuple[str, ...]]:
    """One benign all-defaults case, plus every curated string applied to
    each parameter position in turn (others held at the benign default)."""
    cases: list[tuple[str, ...]] = [tuple(BENIGN_DEFAULT for _ in range(count))]
    for pos in range(count):
        for value in CURATED_STRINGS:
            args = [BENIGN_DEFAULT] * count
            args[pos] = value
            cases.append(tuple(args))
    return cases


def strategy_for(annotation: type) -> st.SearchStrategy:
    """A Hypothesis strategy tuned for the parameter's declared type --
    falling back to the same adversarial-string territory the baseline
    samples from, but exhaustively rather than from a fixed list."""
    if annotation is int:
        return st.integers(min_value=-10_000, max_value=10_000)
    if annotation is float:
        return st.floats(allow_nan=False, allow_infinity=False, width=32)
    # str, or untyped/Any: this is exactly the case where "looks numeric"
    # code (get_user_by_id_unsafe) is actually still string-shaped once a
    # caller hands it a string -- so untyped defaults to text, not int.
    adversarial_chars = st.characters(
        exclude_categories=("Cs",),  # type: ignore[arg-type]  # exclude lone surrogates
        min_codepoint=0x20,
        max_codepoint=0x2764,
    )
    return st.one_of(
        st.just(""),
        st.text(alphabet=adversarial_chars, min_size=0, max_size=60),
        st.sampled_from(CURATED_STRINGS),
    )


def strategy_for_signature(func: Callable) -> st.SearchStrategy:
    """A tuple strategy covering every non-conn parameter of func, in
    order, so a single Hypothesis example is a ready-to-call args tuple."""
    types = param_types(func)
    names = param_names(func)
    return st.tuples(*(strategy_for(types[name]) for name in names))
