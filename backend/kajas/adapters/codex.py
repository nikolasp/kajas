"""Adapter for OpenAI's Codex CLI.

Codex is invoked as ``codex exec --json --model <m> --cd <dir> -``, with
the prompt piped through stdin. ``--json`` causes Codex to emit one
JSON event per line on stdout; we translate those into
:class:`NormalizedEvent` objects.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path

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


@register("codex")
class CodexAdapter(Adapter):
    name = "codex"

    def capabilities(self) -> Capabilities:
        # Codex can enforce sandbox, approval policy, and working dir
        # natively. The network/destructive gates are partial because
        # Codex's "ask" policy can intercept the events but cannot
        # always block; we treat a partial as "good enough for ask,
        # but not for allow/deny".
        return Capabilities(
            sandbox=True,
            approval_policy=True,
            working_dir=True,
            network_gate="partial",
            destructive_gate="partial",
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
        raw_path = raw_dir / "codex.jsonl"
        append_raw(raw_path, f"# run_id={run_id} stage={stage} model={profile_model}")

        # Build a small wrapper that gives Codex the prompt via stdin.
        cmd = _build_command(project_path, profile_model)
        cmd.append("-")  # read prompt from stdin

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=project_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=merged_env,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            # Bubble up as a single error event; the orchestrator records
            # the run as failed.
            return _error_process(self.name, raw_path, f"codex not found: {exc}")

        # Send the prompt and close stdin so codex knows we're done.
        try:
            if proc.stdin is not None:
                with open(prompt_path, "r", encoding="utf-8") as fh:
                    proc.stdin.write(fh.read())
                proc.stdin.close()
        except Exception as exc:  # broken pipe, etc.
            return _error_process(self.name, raw_path, f"failed to feed prompt: {exc}")

        cancelled = {"v": False}

        def _stream() -> Iterator[NormalizedEvent]:
            assert proc.stdout is not None
            stderr_text = ""
            for line in proc.stdout:
                if cancelled["v"]:
                    break
                line = line.rstrip("\n")
                append_raw(raw_path, line)
                ev = _translate(stage, line)
                if ev is not None:
                    yield ev
            # Drain stderr so it doesn't fill the pipe.
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
                    type="log",
                    stage=stage,
                    text=f"codex cancelled (rc={rc})",
                )
            elif rc != 0:
                detail = stderr_text.strip()
                message = f"codex exited with code {rc}"
                if detail:
                    message = f"{message}: {detail}"
                yield NormalizedEvent(
                    type="error",
                    stage=stage,
                    message=message,
                )
            else:
                yield NormalizedEvent(type="log", stage=stage, text="codex finished")

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
        path = which("codex")
        if path is None:
            return HealthResult(ok=False, name=self.name, detail="codex not on PATH")
        try:
            proc = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
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
        # Smoke: run a tiny head request. We avoid spending tokens by
        # just confirming the binary launches and exits 0.
        return HealthResult(
            ok=proc.returncode == 0,
            name=self.name,
            detail=f"smoke ok ({version})",
        )


def _build_command(project_path: Path, profile_model: str) -> list[str]:
    cmd = ["codex", "exec", "--json", "--cd", str(project_path)]
    if not (project_path / ".git").exists():
        cmd.append("--skip-git-repo-check")
    if profile_model and profile_model != "default":
        cmd += ["--model", profile_model]
    return cmd


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _translate(stage: Stage, line: str) -> NormalizedEvent | None:
    """Translate a single Codex JSONL line into a :class:`NormalizedEvent`.

    Codex event types we care about (best-effort, new releases may add
    more - we fall back to a generic ``log`` event):

    * ``thread.started`` / ``turn.started`` - log
    * ``item.created`` with ``item.type == "agent_message"`` - message
    * ``item.created`` with ``item.type == "reasoning"`` - message
    * ``item.created`` with ``item.type == "command_execution"`` - tool_call
    * ``item.created`` with ``item.type == "file_change"`` - tool_call
    * ``item.completed`` with the matching item - tool_result
    * ``turn.completed`` with ``usage`` - usage
    * ``error`` - error
    """
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    etype = data.get("type")

    if etype in {"thread.started", "turn.started"}:
        return NormalizedEvent(type="log", stage=stage, text=str(etype))

    if etype == "item.created":
        item = data.get("item") or {}
        kind = item.get("type")
        if kind in {"agent_message", "reasoning"}:
            return NormalizedEvent(
                type="message",
                stage=stage,
                text=_flatten_text(item.get("text") or item.get("content") or ""),
            )
        if kind == "command_execution":
            return NormalizedEvent(
                type="tool_call",
                stage=stage,
                name="shell",
                summary=str(item.get("command", ""))[:120],
                args={"cmd": item.get("command")},
            )
        if kind == "file_change":
            changes = item.get("changes") or []
            summary = ", ".join(
                f"{c.get('kind', '?')} {c.get('path', '?')}" for c in changes[:3]
            )
            return NormalizedEvent(
                type="tool_call",
                stage=stage,
                name="file_change",
                summary=summary or "file change",
            )
        return None

    if etype == "item.updated":
        return None

    if etype == "item.completed":
        item = data.get("item") or {}
        kind = item.get("type")
        if kind == "agent_message":
            text = _flatten_text(item.get("text") or item.get("content") or "")
            if stage == "planning":
                return NormalizedEvent(
                    type="final",
                    stage=stage,
                    text=text,
                    artifact="plan.md",
                    extra={"plan_yaml": _strip_markdown_fence(text)},
                )
            return NormalizedEvent(
                type="final",
                stage=stage,
                text=text,
                artifact="final.md",
            )
        if kind == "command_execution":
            return NormalizedEvent(
                type="tool_result",
                stage=stage,
                name="shell",
                result=(item.get("aggregated_output") or "")[:500],
            )
        if kind == "file_change":
            return NormalizedEvent(
                type="tool_result",
                stage=stage,
                name="file_change",
                result="applied",
            )
        return None

    if etype == "turn.completed":
        usage = data.get("usage") or {}
        inp = _as_int(usage.get("input_tokens"))
        out = _as_int(usage.get("output_tokens"))
        return NormalizedEvent(
            type="usage",
            stage=stage,
            input_tokens=inp,
            output_tokens=out,
            total_tokens=(inp or 0) + (out or 0) if inp is not None or out is not None else None,
        )

    if etype == "error":
        msg = data.get("message") or data.get("error") or "codex error"
        return NormalizedEvent(type="error", stage=stage, message=str(msg))

    return None


def _flatten_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip() + "\n"
    return text


def _as_int(v: object) -> int | None:
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _error_process(name: str, raw_path: Path, message: str) -> AdapterProcess:
    def _stream() -> Iterator[NormalizedEvent]:
        yield NormalizedEvent(type="error", stage="planning", message=message)

    return AdapterProcess(name=name, raw_path=raw_path, events=_stream())


__all__ = ["CodexAdapter"]
