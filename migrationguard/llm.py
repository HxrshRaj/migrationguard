"""Thin LLM client abstraction + trajectory disclosure logging.

Every advanced-mode stage (scanner explanation, fix generation, severity
rationale) calls the model through one LLMClient. That is what makes
trajectory disclosure complete by construction: the logging happens once,
here, so there is no code path that reaches Claude without also being
recorded to trajectories.jsonl.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

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


@dataclass
class AnthropicLLMClient:
    """The real client. Requires ANTHROPIC_API_KEY in the environment."""

    trajectory_log: TrajectoryLog
    model: str = DEFAULT_MODEL
    _client: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        import anthropic  # local import: keep the dependency optional at module load

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Advanced mode calls Claude for "
                "explanation, fix generation, and severity rationale -- set "
                "the key, or run with `--mode baseline`."
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, *, stage: str, system: str, prompt: str) -> str:
        call_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1536,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        latency_ms = (time.perf_counter() - start) * 1000
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        self.trajectory_log.record(
            LLMTrajectory(
                call_id=call_id,
                stage=stage,
                model=self.model,
                prompt=prompt,
                response=text,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                latency_ms=latency_ms,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        return text


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
