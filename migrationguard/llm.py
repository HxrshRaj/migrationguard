"""Thin LLM client abstraction + trajectory disclosure logging.

Every advanced-mode stage (scanner explanation, fix generation, severity
rationale) calls the model through one LLMClient. That is what makes
trajectory disclosure complete by construction: the logging happens once,
here, so there is no code path that reaches Claude without also being
recorded to trajectories.jsonl.
"""
from __future__ import annotations

import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

from migrationguard.models import LLMTrajectory


class LLMClient(Protocol):
    def complete(self, *, stage: str, system: str, prompt: str) -> str: ...


@dataclass
class TrajectoryLog:
    """Append-only JSONL log of every LLM call made during a run. This file
    *is* the "agent trajectories" submission artifact -- ship it as-is."""

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, trajectory: LLMTrajectory) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(trajectory.model_dump_json() + "\n")


DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

# HTTP statuses worth another attempt: rate limiting, transient conflict,
# and the 5xx family (529 = Anthropic "overloaded").
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class LLMCallError(RuntimeError):
    """Raised when an LLM call fails in a way that should stop the whole
    run (an auth/permission error -- nothing downstream will work). A
    transient failure that survives every retry does NOT raise this: it
    degrades that one call (see AnthropicLLMClient.complete)."""


@dataclass
class AnthropicLLMClient:
    """The real client. Requires ANTHROPIC_API_KEY in the environment.

    Wraps every call in bounded exponential backoff (on top of the SDK's
    own retries). A call that still fails after `max_attempts` transient
    errors does not crash the run -- it records the failure in
    `self.failures`, writes a clearly-marked trajectory entry, and returns
    "" so the caller degrades gracefully (a missing explanation falls back
    to the canned one, a missing fix is reported as not-auto-fixed, a
    missing rationale falls back to the heuristic). Only an auth /
    permission error raises (LLMCallError) -- there is no point retrying
    or continuing past that.
    """

    trajectory_log: TrajectoryLog
    model: str = DEFAULT_MODEL
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0
    client: Optional[object] = None  # injectable for tests; real client built if None
    _client: object = field(init=False, repr=False)
    failures: list[str] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.client is not None:
            self._client = self.client
            return
        import anthropic  # local import: keep the dependency optional at module load

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Advanced mode calls Claude for "
                "explanation, fix generation, and severity rationale -- set "
                "the key, or run with `--mode baseline`."
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def _sleep(self, seconds: float) -> None:  # seam: tests override this
        time.sleep(seconds)

    def _classify(self, exc: Exception) -> str:
        """'retry' | 'degrade' | 'raise' for an exception from the SDK."""
        import anthropic

        if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return "retry"
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return "raise"
        if isinstance(exc, anthropic.APIStatusError):
            return "retry" if getattr(exc, "status_code", None) in _RETRYABLE_STATUS else "degrade"
        return "raise"  # not an API error we know how to reason about -- surface it

    def complete(self, *, stage: str, system: str, prompt: str) -> str:
        call_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=1536,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:  # noqa: BLE001 - re-raised unless known-transient
                verdict = self._classify(exc)
                if verdict == "raise":
                    raise LLMCallError(f"{stage}: {type(exc).__name__}: {exc}") from exc
                last_exc = exc
                if verdict == "degrade" or attempt == self.max_attempts:
                    break
                self._sleep(
                    min(self.base_delay * 2 ** (attempt - 1), self.max_delay)
                    + random.uniform(0, self.base_delay)
                )
                continue

            latency_ms = (time.perf_counter() - start) * 1000
            text = "".join(
                block.text
                for block in response.content
                if getattr(block, "type", "") == "text"
            )
            usage = getattr(response, "usage", None)
            self._record(
                call_id,
                stage,
                prompt,
                text,
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                latency_ms,
            )
            return text

        reason = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "unknown error"
        self.failures.append(f"{stage}: {reason}")
        self._record(
            call_id,
            stage,
            prompt,
            f"<CALL FAILED after {self.max_attempts} attempt(s) -- {reason}>",
            None,
            None,
            (time.perf_counter() - start) * 1000,
        )
        return ""

    def _record(
        self,
        call_id: str,
        stage: str,
        prompt: str,
        response: str,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        latency_ms: float,
    ) -> None:
        self.trajectory_log.record(
            LLMTrajectory(
                call_id=call_id,
                stage=stage,
                model=self.model,
                prompt=prompt,
                response=response,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )


@dataclass
class FakeLLMClient:
    """Deterministic stand-in used by the test suite (and by anyone running
    `--mode advanced` without an API key, e.g. for a first dry run). A
    `responder(stage, system, prompt) -> str` callback supplies the canned
    reply, so the pipeline logic around the model -- not the model's
    judgment -- is what gets tested. Still writes a trajectory entry,
    tagged model="fake-llm-client", so a run using it is never mistaken
    for a real Claude call in the disclosed log."""

    trajectory_log: TrajectoryLog
    responder: Callable[[str, str, str], str] = field(default=lambda s, sy, p: "")
    model: str = "fake-llm-client"

    def complete(self, *, stage: str, system: str, prompt: str) -> str:
        text = self.responder(stage, system, prompt)
        self.trajectory_log.record(
            LLMTrajectory(
                call_id=str(uuid.uuid4())[:8],
                stage=stage,
                model=self.model,
                prompt=prompt,
                response=text,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        return text
