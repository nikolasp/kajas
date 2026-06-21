"""Adapter base classes and shared types.

Adapters are responsible for:

1. Taking a stage (``planning`` or ``implementation``), the run, the
   agent profile, and the path to a prompt file.
2. Returning an :class:`AdapterProcess` that the orchestrator can
   ``stream()`` and ``cancel()``.

The :class:`NormalizedEvent` model is the wire format we use both
internally (for the SSE stream) and for storing in ``events.ndjson``.
"""

from __future__ import annotations

import enum
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

# ``Stage`` is the v1 workflow's two agent-driven stages. Verification
# is run by the orchestrator itself, not by an adapter.
Stage = Literal["planning", "implementation"]


class NormalizedEvent(BaseModel):
    """An adapter-agnostic event.

    Adapters translate whatever their tool emits into this shape. The
    ``extra`` bucket lets adapters carry tool-specific data without
    having to grow the schema for every quirk.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "message",
        "tool_call",
        "tool_result",
        "approval_request",
        "usage",
        "artifact",
        "final",
        "error",
        "log",
        "status",
    ]
    stage: Stage
    status: str | None = None
    text: str | None = None
    name: str | None = None  # for tool_call/tool_result
    summary: str | None = None
    args: dict[str, Any] | None = None  # for tool_call
    result: str | None = None  # for tool_result
    reason: str | None = None  # for approval_request
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    artifact: str | None = None  # for final/artifact events
    message: str | None = None  # for error
    ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass
class HealthResult:
    ok: bool
    name: str
    detail: str = ""
    raw_event: NormalizedEvent | None = None


class Capabilities(BaseModel):
    """What policy fields the adapter can enforce."""

    model_config = ConfigDict(extra="forbid")

    sandbox: bool = False
    approval_policy: bool = False
    working_dir: bool = False
    network_gate: bool | Literal["partial"] = False
    destructive_gate: bool | Literal["partial"] = False


@dataclass
class AdapterProcess:
    """Handle returned by ``Adapter.start``.

    ``events`` is a blocking iterator that yields :class:`NormalizedEvent`
    objects. ``cancel`` is best-effort: it should send a graceful
    terminate, wait briefly, then SIGKILL.
    """

    name: str
    raw_path: Path  # where raw tool output is being written
    events: Iterator[NormalizedEvent] | None = None
    _cancel: Any = None
    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._cancel is not None:
            try:
                self._cancel()
            except Exception:
                pass


class Adapter:
    """Abstract base for adapters.

    Subclasses must implement :meth:`start` and :meth:`capabilities`, and
    may override :meth:`doctor` for adapter-specific health checks.
    """

    name: str = "base"

    def capabilities(self) -> Capabilities:
        raise NotImplementedError

    def start(
        self,
        *,
        stage: Stage,
        run_id: str,
        project_path: Path,
        profile_model: str,
        prompt_path: Path,
        raw_dir: Path,
        env: dict[str, str] | None = None,
    ) -> AdapterProcess:
        raise NotImplementedError

    def doctor(self, *, smoke: bool = False, timeout: float = 30.0) -> HealthResult:
        """Run adapter-specific health checks. ``smoke=True`` runs a real
        tool invocation that may consume tokens; the Web UI warns the
        user before enabling it."""
        return HealthResult(ok=True, name=self.name, detail="no checks defined")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_BUILTINS: dict[str, type[Adapter]] = {}


def register(name: str) -> Any:
    def _wrap(cls: type[Adapter]) -> type[Adapter]:
        cls.name = name
        _BUILTINS[name] = cls
        return cls

    return _wrap


def load_registry(tool_names: Iterable[str]) -> dict[str, Adapter]:
    """Build a registry mapping tool name -> adapter instance.

    Unknown tools raise ``KeyError``; the caller (the doctor and the run
    orchestrator) is responsible for reporting the missing adapter as a
    health issue and for refusing to start a run.
    """
    instances: dict[str, Adapter] = {}
    for name in tool_names:
        if name in instances:
            continue
        cls = _BUILTINS.get(name)
        if cls is None:
            continue
        instances[name] = cls()
    return instances


def _raw_event_line(stage: Stage, payload: dict[str, Any]) -> str:
    """Serialise a raw dict as a JSON line for ``raw/<tool>.jsonl``."""
    payload = {"stage": stage, **payload}
    return json.dumps(payload, ensure_ascii=False)


def append_raw(raw_path: Path, line: str) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        if not line.endswith("\n"):
            fh.write("\n")


def which(cmd: str) -> str | None:
    """Look up ``cmd`` on ``$PATH``; return the path or ``None``."""
    return shutil.which(cmd)


__all__ = [
    "Adapter",
    "AdapterProcess",
    "Capabilities",
    "HealthResult",
    "NormalizedEvent",
    "Stage",
    "append_raw",
    "load_registry",
    "register",
    "which",
]
