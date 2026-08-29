"""AnthropicLLMClient's retry / backoff / graceful-degradation behaviour.

Uses an injected fake transport (`client=...`) so nothing here touches the
network or needs an API key.
"""
from __future__ import annotations

import json

import pytest

from migrationguard.llm import AnthropicLLMClient, LLMCallError, TrajectoryLog


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    input_tokens = 11
    output_tokens = 7


class _Resp:
    def __init__(self, text: str = "ok") -> None:
        self.content = [_Block(text)]
        self.usage = _Usage()


class _FlakyMessages:
    def __init__(self, fail_times: int, exc: Exception) -> None:
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    def create(self, **_kw) -> _Resp:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return _Resp()


class _FakeClient:
    def __init__(self, messages: _FlakyMessages) -> None:
        self.messages = messages


def _make(tmp_path, messages, verdict):
    log = TrajectoryLog(tmp_path / "traj.jsonl")
    log.path.write_text("", encoding="utf-8")
    client = AnthropicLLMClient(
        log, client=_FakeClient(messages), max_attempts=4, base_delay=0.0
    )
    slept: list[float] = []
    client._sleep = slept.append  # type: ignore[assignment]
    if verdict is not None:
        client._classify = lambda _exc: verdict  # type: ignore[assignment]
    return client, log, slept


def _traj_lines(log) -> list[dict]:
    return [json.loads(x) for x in log.path.read_text().splitlines() if x.strip()]


def test_succeeds_after_transient_failures(tmp_path):
    msgs = _FlakyMessages(2, RuntimeError("boom"))
    client, log, slept = _make(tmp_path, msgs, verdict="retry")

    out = client.complete(stage="scanner.explain", system="s", prompt="p")

    assert out == "ok"
    assert msgs.calls == 3  # 2 failures + 1 success
    assert len(slept) == 2  # backed off between attempts
    assert client.failures == []
    lines = _traj_lines(log)
    assert len(lines) == 1 and lines[0]["response"] == "ok"
    assert lines[0]["input_tokens"] == 11


def test_degrades_to_empty_string_after_exhausting_retries(tmp_path):
    msgs = _FlakyMessages(99, RuntimeError("still down"))
    client, log, slept = _make(tmp_path, msgs, verdict="retry")

    out = client.complete(stage="fixgen.rewrite", system="s", prompt="p")

    assert out == ""  # degraded, not raised
    assert msgs.calls == 4  # capped at max_attempts
    assert len(slept) == 3  # one fewer sleep than attempts
    assert len(client.failures) == 1
    assert "RuntimeError" in client.failures[0] and "fixgen.rewrite" in client.failures[0]
    (line,) = _traj_lines(log)
    assert line["response"].startswith("<CALL FAILED after 4 attempt(s)")


def test_non_retryable_status_degrades_immediately_without_sleeping(tmp_path):
    msgs = _FlakyMessages(99, RuntimeError("400"))
    client, _log, slept = _make(tmp_path, msgs, verdict="degrade")

    out = client.complete(stage="diffengine.rationale", system="s", prompt="p")

    assert out == ""
    assert msgs.calls == 1
    assert slept == []
    assert len(client.failures) == 1


def test_auth_error_raises_and_stops_the_run(tmp_path):
    msgs = _FlakyMessages(99, RuntimeError("401"))
    client, _log, _slept = _make(tmp_path, msgs, verdict="raise")

    with pytest.raises(LLMCallError):
        client.complete(stage="scanner.explain", system="s", prompt="p")
    assert msgs.calls == 1


def test_default_classify_maps_real_anthropic_exceptions(tmp_path):
    import anthropic

    httpx2 = pytest.importorskip("httpx2")
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def resp(status: int):
        return httpx2.Response(status, request=req)

    client, _log, _slept = _make(tmp_path, _FlakyMessages(0, RuntimeError()), verdict=None)

    assert client._classify(anthropic.APITimeoutError(request=req)) == "retry"
    assert client._classify(anthropic.APIConnectionError(request=req)) == "retry"
    assert client._classify(anthropic.RateLimitError("x", response=resp(429), body=None)) == "retry"
    assert client._classify(anthropic.InternalServerError("x", response=resp(500), body=None)) == "retry"
    assert client._classify(anthropic.BadRequestError("x", response=resp(400), body=None)) == "degrade"
    assert client._classify(anthropic.AuthenticationError("x", response=resp(401), body=None)) == "raise"
    assert client._classify(ValueError("not an API error")) == "raise"
