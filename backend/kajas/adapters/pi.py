"""Adapter for Pi, the earendil-works coding agent.

Pi is invoked as ``pi --print --mode json --model <m> @<prompt_file> <msg>``.
The ``--mode json`` flag emits one JSON event per line, which we
translate into :class:`NormalizedEvent` objects.

Pi's event types:

* ``session`` - first event, ignored (carries cwd, id, version).
* ``agent_start`` / ``agent_end`` - log
* ``turn_start`` / ``turn_end`` - log; ``turn_end`` carries usage + tool
  results.
* ``message_start`` / ``message_end`` - bookends of a single message.
  The assistant message carries a ``content`` array of typed blocks:
  ``{"type": "text", "text": "..."}`` and
  ``{"type": "thinking", "thinking": "..."}``.
* ``message_update`` - a streaming chunk. ``assistantMessageEvent.type``
  is one of ``text_start``/``text_delta``/``text_end``/
  ``thinking_start``/``thinking_delta``/``thinking_end``.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from .base import (
    Adapter,
    AdapterProcess,
    Capabilities,
    HealthResult,
    NormalizedEvent,
    Stage,
    append_raw,
    register,
    which,
)


@register("pi")
class PiAdapter(Adapter):
    name = "pi"

    def capabilities(self) -> Capabilities:
        # Pi has no built-in policy enforcement. The ``supports`` table
        # in the v1 design says so explicitly, so the orchestrator will
        # refuse to start a run with a non-allow policy unless
        # ``allow_unenforced_policy`` is set.
        return Capabilities(
            sandbox=False,
            approval_policy=False,
            working_dir=True,
            network_gate=False,
            destructive_gate=False,
        )

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
        raw_path = raw_dir / "pi.jsonl"
        append_raw(raw_path, f"# run_id={run_id} stage={stage} model={profile_model}")

        cmd = ["pi", "--print", "--mode", "json"]
        if profile_model and profile_model != "default":
            cmd += ["--model", profile_model]
        # ``@<file>`` includes the prompt file as a user message, and
        # we add an explicit instruction so pi knows to read the brief.
        cmd += [f"@{prompt_path}", "Follow the brief in the attached file."]

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=project_path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=merged_env,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            return _error_process(self.name, raw_path, f"pi not found: {exc}")

        cancelled = {"v": False}

        # We accumulate ``message_end`` payloads so we can summarise the
        # final assistant text without re-reading the entire stream.
        def _stream() -> Iterator[NormalizedEvent]:
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancelled["v"]:
                    break
                line = line.rstrip("\n")
                append_raw(raw_path, line)
                for ev in _translate(stage, line):
                    yield ev

            if proc.stderr is not None:
                stderr_text = proc.stderr.read() or ""
                if stderr_text:
                    append_raw(
                        raw_path,
                        json.dumps({"type": "_stderr", "text": stderr_text}),
                    )
            rc = proc.wait()
            if cancelled["v"]:
                yield NormalizedEvent(
                    type="log", stage=stage, text=f"pi cancelled (rc={rc})"
                )
            elif rc != 0:
                yield NormalizedEvent(
                    type="error", stage=stage, message=f"pi exited with code {rc}"
                )
            else:
                yield NormalizedEvent(type="log", stage=stage, text="pi finished")

        def _cancel() -> None:
            cancelled["v"] = True
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        return AdapterProcess(
            name=self.name,
            raw_path=raw_path,
            events=_stream(),
            _cancel=_cancel,
        )

    def doctor(self, *, smoke: bool = False, timeout: float = 30.0) -> HealthResult:
        path = which("pi")
        if path is None:
            return HealthResult(ok=False, name=self.name, detail="pi not on PATH")
        try:
            proc = subprocess.run(
                ["pi", "--version"], capture_output=True, text=True, timeout=5
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return HealthResult(ok=False, name=self.name, detail=str(exc))
        version = (proc.stdout or proc.stderr or "").strip()
        if not smoke:
            return HealthResult(
                ok=proc.returncode == 0,
                name=self.name,
                detail=f"{path} ({version})",
            )
        return HealthResult(
            ok=proc.returncode == 0,
            name=self.name,
            detail=f"smoke ok ({version})",
        )


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _translate(stage: Stage, line: str) -> list[NormalizedEvent]:
    if not line.startswith("{"):
        return []
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []
    etype = data.get("type")
    out: list[NormalizedEvent] = []

    if etype == "session":
        # Carry the pi session id through so the UI can show it.
        out.append(
            NormalizedEvent(
                type="log",
                stage=stage,
                text=f"pi session {data.get('id', '?')}",
            )
        )
        return out

    if etype in {"agent_start", "agent_end", "turn_start", "turn_end"}:
        # ``turn_end`` carries the per-turn usage and tool results; we
        # unpack them in the body below.
        if etype == "turn_end":
            msg = data.get("message") or {}
            usage = msg.get("usage") or {}
            inp = _as_int(usage.get("input"))
            out_t = _as_int(usage.get("output"))
            cache_read = _as_int(usage.get("cacheRead"))
            cache_write = _as_int(usage.get("cacheWrite"))
            total = _as_int(usage.get("totalTokens"))
            if inp is not None or out_t is not None or total is not None:
                out.append(
                    NormalizedEvent(
                        type="usage",
                        stage=stage,
                        input_tokens=inp,
                        output_tokens=out_t,
                        total_tokens=total if total is not None else ((inp or 0) + (out_t or 0)),
                        extra={
                            "cacheRead": cache_read,
                            "cacheWrite": cache_write,
                        },
                    )
                )
            for tr in (msg.get("toolResults") or []):
                out.append(
                    NormalizedEvent(
                        type="tool_result",
                        stage=stage,
                        name=_tool_name(tr),
                        result=_tool_result_text(tr),
                        extra={"tr": tr},
                    )
                )
            out.append(NormalizedEvent(type="log", stage=stage, text="turn ended"))
        else:
            out.append(NormalizedEvent(type="log", stage=stage, text=str(etype)))
        return out

    if etype == "message_start":
        msg = data.get("message") or {}
        if msg.get("role") != "assistant":
            return out
        # Reset accumulated text/thinking for this assistant message.
        _accum.setdefault(stage, {"text": "", "thinking": ""})
        return out

    if etype == "message_update":
        sub = data.get("assistantMessageEvent") or {}
        kind = sub.get("type")
        if kind in {"text_start", "text_delta", "text_end"}:
            delta = sub.get("delta") or sub.get("content") or ""
            accum = _accum.setdefault(stage, {"text": "", "thinking": ""})
            if kind == "text_delta":
                accum["text"] += str(delta)
            elif kind == "text_start":
                accum["text"] = str(delta) if delta else accum["text"]
            elif kind == "text_end":
                accum["text"] = str(sub.get("content", "")) or accum["text"]
        elif kind in {"thinking_start", "thinking_delta", "thinking_end"}:
            delta = sub.get("delta") or sub.get("content") or ""
            accum = _accum.setdefault(stage, {"text": "", "thinking": ""})
            if kind == "thinking_delta":
                accum["thinking"] += str(delta)
            elif kind == "thinking_start":
                accum["thinking"] = str(delta) if delta else accum["thinking"]
            elif kind == "thinking_end":
                accum["thinking"] = str(sub.get("content", "")) or accum["thinking"]
        return out

    if etype == "message_end":
        msg = data.get("message") or {}
        if msg.get("role") != "assistant":
            return out
        accum = _accum.get(stage, {"text": "", "thinking": ""})
        text = _flatten_text(msg.get("content")) or accum.get("text", "")
        thinking = accum.get("thinking", "")
        if thinking:
            out.append(
                NormalizedEvent(
                    type="message", stage=stage, text=f"[thinking]\n{thinking}"
                )
            )
        if text:
            out.append(NormalizedEvent(type="message", stage=stage, text=text))
        _accum.pop(stage, None)
        return out

    return out


# Module-level accumulator: pi streams deltas within a single assistant
# message and only carries the full text on ``message_end``. The key is
# the stage; we never run two stages concurrently in a single process.
_accum: dict[str, dict[str, str]] = {}


def _flatten_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                kind = item.get("type")
                if kind == "text":
                    parts.append(str(item.get("text", "")))
                elif kind == "thinking":
                    parts.append(f"[thinking] {item.get('thinking','')}")
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _as_int(v: object) -> int | None:
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _tool_name(tr: dict[str, Any]) -> str:
    # Pi's tool result shape varies; we use ``toolName`` first, then a
    # best-effort inference.
    if "toolName" in tr:
        return str(tr["toolName"])
    if "name" in tr:
        return str(tr["name"])
    return "tool"


def _tool_result_text(tr: dict[str, Any]) -> str:
    for key in ("output", "result", "text", "content"):
        if key in tr and tr[key] is not None:
            value = tr[key]
            if isinstance(value, str):
                return value[:500]
            return json.dumps(value)[:500]
    return "(no output)"


def _error_process(name: str, raw_path: Path, message: str) -> AdapterProcess:
    def _stream() -> Iterator[NormalizedEvent]:
        yield NormalizedEvent(type="error", stage="planning", message=message)

    return AdapterProcess(name=name, raw_path=raw_path, events=_stream())


__all__ = ["PiAdapter"]
