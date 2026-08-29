"""The per-run cost estimate. Token totals are always exact (they come
straight from trajectories.jsonl); only the dollar figure is approximate,
and only because a published rate can go stale."""
from __future__ import annotations

import json

from migrationguard.cost import PRICES, estimate_run_cost, format_cost_summary


def _write(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_missing_or_empty_file_is_zero_cost(tmp_path):
    missing = estimate_run_cost(tmp_path / "nope.jsonl")
    assert missing.calls == 0 and missing.usd == 0.0 and not missing.is_billable

    empty = tmp_path / "t.jsonl"
    empty.write_text("", encoding="utf-8")
    assert not estimate_run_cost(empty).is_billable


def test_fake_llm_calls_are_counted_but_never_billable(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, [
        {"model": "fake-llm-client", "input_tokens": None, "output_tokens": None},
        {"model": "fake-llm-client"},
    ])
    cost = estimate_run_cost(p)
    assert cost.calls == 2
    assert cost.priced_calls == 0
    assert cost.usd == 0.0
    assert not cost.is_billable  # keeps --fake-llm CLI output identical


def test_priced_model_totals_match_a_hand_computation(tmp_path):
    p = tmp_path / "t.jsonl"
    model = "claude-sonnet-4-5-20250929"
    in_rate, out_rate = PRICES[model]
    _write(p, [
        {"model": model, "input_tokens": 1_000_000, "output_tokens": 0},
        {"model": model, "input_tokens": 0, "output_tokens": 500_000},
    ])
    cost = estimate_run_cost(p)
    assert cost.calls == 2 and cost.priced_calls == 2
    assert cost.input_tokens == 1_000_000
    assert cost.output_tokens == 500_000
    assert cost.usd == in_rate + out_rate / 2
    assert cost.is_billable
    assert f"${cost.usd:.4f}" in format_cost_summary(cost)


def test_unknown_model_is_flagged_not_silently_priced_at_zero(tmp_path):
    p = tmp_path / "t.jsonl"
    _write(p, [{"model": "claude-from-the-future", "input_tokens": 10, "output_tokens": 20}])
    cost = estimate_run_cost(p)
    assert cost.priced_calls == 0
    assert cost.usd == 0.0
    assert cost.unpriced_models == {"claude-from-the-future"}
    assert cost.is_billable  # so the CLI still surfaces "no rate on file"
    assert "no rate on file" in format_cost_summary(cost)


def test_malformed_lines_are_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('not json\n{"model": "fake-llm-client"}\n', encoding="utf-8")
    cost = estimate_run_cost(p)
    assert cost.calls == 1
