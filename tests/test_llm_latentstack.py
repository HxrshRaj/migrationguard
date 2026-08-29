"""LatentStackLLMClient's retry / backoff / graceful-degradation behaviour.

Uses an injected fake transport (`client=...`) so nothing here touches the
network or needs an API key.
"""
from __future__ import annotations

import json

import pytest

from migrationguard.llm import LatentStackLLMClient, LLMCallError, TrajectoryLog


class _Message:
    def __init__(self, text: str) -> None:
        self.content = text


class _Choice:
    def __init__(self, text: str) -> None:
        self.message = _Message(text)


class _Usage:
    prompt_tokens = 11
    completion_tokens = 7


class _Resp:
    def __init__(self, text: str = "ok") -> None:
        self.choices = [_Choice(text)]
        self.usage = _Usage()


class _FlakyCompletions:
    def __init__(self, fail_times: int, exc: Exception) -> None:
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    def create(self, **_kw) -> _Resp:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return _Resp()


class _FakeChat:
    def __init__(self, completions: _FlakyCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FlakyCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make(tmp_path, completions, verdict):
    log = TrajectoryLog(tmp_path / "traj.jsonl")
    log.path.write_text("", encoding="utf-8")
    client = LatentStackLLMClient(
        log, client=_FakeClient(completions), max_attempts=4, base_delay=0.0
    )
    slept: list[float] = []
    client._sleep = slept.append  # type: ignore[assignment]
    if verdict is not None:
        client._classify = lambda _exc: verdict  # type: ignore[assignment]
    return client, log, slept


def _traj_lines(log) -> list[dict]:
    return [json.loads(x) for x in log.path.read_text().splitlines() if x.strip()]


def test_succeeds_after_transient_failures(tmp_path):
    msgs = _FlakyCompletions(2, RuntimeError("boom"))
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
    msgs = _FlakyCompletions(99, RuntimeError("still down"))
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
    msgs = _FlakyCompletions(99, RuntimeError("400"))
    client, _log, slept = _make(tmp_path, msgs, verdict="degrade")

    out = client.complete(stage="diffengine.rationale", system="s", prompt="p")

    assert out == ""
    assert msgs.calls == 1
    assert slept == []
    assert len(client.failures) == 1


def test_auth_error_raises_and_stops_the_run(tmp_path):
    msgs = _FlakyCompletions(99, RuntimeError("401"))
    client, _log, _slept = _make(tmp_path, msgs, verdict="raise")

    with pytest.raises(LLMCallError):
        client.complete(stage="scanner.explain", system="s", prompt="p")
    assert msgs.calls == 1


def test_default_classify_maps_real_openai_exceptions(tmp_path):
    import openai

    httpx = pytest.importorskip("httpx")
    req = httpx.Request("POST", "https://latentstack.dev/v1/chat/completions")

    def resp(status: int):
        return httpx.Response(status, request=req)

    client, _log, _slept = _make(tmp_path, _FlakyCompletions(0, RuntimeError()), verdict=None)

    assert client._classify(openai.APITimeoutError(request=req)) == "retry"
    assert client._classify(openai.APIConnectionError(request=req)) == "retry"
    assert client._classify(openai.RateLimitError("x", response=resp(429), body=None)) == "retry"
    assert client._classify(openai.InternalServerError("x", response=resp(500), body=None)) == "retry"
    assert client._classify(openai.BadRequestError("x", response=resp(400), body=None)) == "degrade"
    assert client._classify(openai.AuthenticationError("x", response=resp(401), body=None)) == "raise"
    assert client._classify(openai.PermissionDeniedError("x", response=resp(403), body=None)) == "raise"
    assert client._classify(ValueError("not an API error")) == "raise"
