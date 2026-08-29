"""Rough LLM cost estimation from a run's `trajectories.jsonl`.

`trajectories.jsonl` already records the exact `input_tokens` /
`output_tokens` every real Claude call reported. This module does one
thing: multiply those recorded counts by a published per-million-token
rate and total them, so a run can print what it approximately cost
without anyone having to open the pricing page and do the arithmetic.

The rates in `PRICES` are entered by hand from Anthropic's public
pricing page (https://www.anthropic.com/pricing, checked 2026-08-29).
They are the one thing here that can silently go stale -- update them if
they have. Everything else is derived from the run's own recorded
token counts, so the token totals are always exact even when the dollar
figure is approximate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# USD per 1,000,000 tokens, as (input_rate, output_rate).
PRICES: dict[str, tuple[float, float]] = {
    # AnthropicLLMClient's default target (see migrationguard/llm.py).
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "gemini/gemini-3.1-pro": (1.25, 10.00),  # TODO confirm against LatentStack published gateway pricing
}

# Models that cost nothing to call -- the deterministic stand-in used by
# `--fake-llm`. Counted, but never priced or flagged as unpriced.
_FREE_MODELS = {"fake-llm-client"}


@dataclass
class RunCost:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    priced_calls: int = 0
    unpriced_models: set[str] = field(default_factory=set)

    @property
    def is_billable(self) -> bool:
        """True when this run actually made a metered LLM call -- so the
        CLI can stay byte-for-byte identical in baseline / --fake-llm
        modes, which make no billable calls."""
        return bool(self.input_tokens or self.output_tokens or self.unpriced_models)


def estimate_run_cost(trajectories_path: Path) -> RunCost:
    cost = RunCost()
    if not trajectories_path.exists():
        return cost
    for line in trajectories_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        cost.calls += 1
        model = entry.get("model", "")
        if model in _FREE_MODELS:
            continue
        in_tok = int(entry.get("input_tokens") or 0)
        out_tok = int(entry.get("output_tokens") or 0)
        cost.input_tokens += in_tok
        cost.output_tokens += out_tok
        rate = PRICES.get(model)
        if rate is None:
            cost.unpriced_models.add(model or "<unknown>")
            continue
        in_rate, out_rate = rate
        cost.usd += in_tok / 1_000_000 * in_rate + out_tok / 1_000_000 * out_rate
        cost.priced_calls += 1
    return cost


def format_cost_summary(cost: RunCost) -> str:
    summary = (
        f"LLM cost:    ~${cost.usd:.4f} over {cost.priced_calls} priced call(s) "
        f"({cost.input_tokens:,} in + {cost.output_tokens:,} out tokens)"
    )
    if cost.unpriced_models:
        summary += (
            f"; no rate on file for {', '.join(sorted(cost.unpriced_models))} "
            f"(update migrationguard/cost.py:PRICES)"
        )
    return summary
