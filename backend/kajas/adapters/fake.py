"""In-process fake adapter used in tests and during Milestone 1.

The fake adapter simulates a deterministic sequence of normalized
events for a given run prompt. It is a "first-class test double", not a
UI-only mock: it writes ``raw/fake.jsonl``, it can be configured to
emit tool calls, request approvals, fail, or amend the plan, and it
reports honest token usage.
"""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Iterator
from pathlib import Path
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
)


# Behaviour modifiers, read from the agent profile's ``extra`` block.
#
#   mode: "happy" (default) | "amend" | "ask" | "fail"
#   tokens_in / tokens_out: override the auto-computed token counts.
#   delay: seconds between events (float, default 0.0).
DEFAULT_BEHAVIOUR = "happy"


def _behaviour(extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(extra or {})
    out.setdefault("mode", DEFAULT_BEHAVIOUR)
    out.setdefault("delay", 0.0)
    out.setdefault("tokens_in", None)
    out.setdefault("tokens_out", None)
    return out


def _simulated_usage(prompt: str) -> tuple[int, int]:
    """Cheap deterministic token estimate so the fake still produces numbers."""
    h = hashlib.sha256(prompt.encode("utf-8")).digest()
    inp = 200 + (h[0] << 8 | h[1])
    out = 80 + (h[2] << 8 | h[3])
    return inp, out


@register("fake")
class FakeAdapter(Adapter):
    name = "fake"

    def capabilities(self) -> Capabilities:
        # Fake can pretend to enforce everything so we can exercise the
        # full policy gate from the UI without a real tool.
        return Capabilities(
            sandbox=True,
            approval_policy=True,
            working_dir=True,
            network_gate=True,
            destructive_gate=True,
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
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
        extra = _behaviour(_parse_prompt_meta(prompt))
        raw_path = raw_dir / "fake.jsonl"
        append_raw(raw_path, f"# run_id={run_id} stage={stage} model={profile_model}")

        # The fake's behaviour is shaped by per-run hints embedded in
        # the prompt's ``<!-- kajas:fake ... -->`` block. This keeps the
        # orchestrator simple: it always writes the prompt to disk, and
        # the fake inspects it.
        events = list(_build_events(stage, prompt, extra, raw_path))

        def _stream() -> Iterator[NormalizedEvent]:
            for ev in events:
                time.sleep(float(extra.get("delay") or 0.0))
                if cancelled["v"]:
                    return
                yield ev

        cancelled: dict[str, bool] = {"v": False}

        def _cancel() -> None:
            cancelled["v"] = True

        return AdapterProcess(
            name=self.name,
            raw_path=raw_path,
            events=_stream(),
            _cancel=_cancel,
        )

    def doctor(self, *, smoke: bool = False, timeout: float = 30.0) -> HealthResult:
        if not smoke:
            return HealthResult(ok=True, name=self.name, detail="fake adapter always healthy")
        # In smoke mode, run a real but tiny fake pass.
        ev = NormalizedEvent(
            type="message",
            stage="planning",
            text="smoke check",
        )
        return HealthResult(ok=True, name=self.name, detail="smoke ok", raw_event=ev)


# ---------------------------------------------------------------------------
# Prompt meta parsing + event construction
# ---------------------------------------------------------------------------


def _parse_prompt_meta(prompt: str) -> dict[str, Any]:
    """Pull hints out of the prompt's ``<!-- kajas:fake ... -->`` block.

    The orchestrator never writes this block; tests, the New Run UI, or
    the doctor can append it to nudge the fake.
    """
    import re

    m = re.search(r"<!--\s*kajas:fake\s*(.*?)\s*-->", prompt, flags=re.DOTALL)
    if not m:
        return {}
    out: dict[str, Any] = {}
    for token in m.group(1).split():
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
        else:
            out[token] = True
    return out


def _build_events(
    stage: Stage, prompt: str, extra: dict[str, Any], raw_path: Path
) -> list[NormalizedEvent]:
    mode = str(extra.get("mode", DEFAULT_BEHAVIOUR))
    events: list[NormalizedEvent] = []

    def add(ev: NormalizedEvent) -> None:
        append_raw(raw_path, ev.model_dump_json())
        events.append(ev)

    if stage == "planning":
        add(
            NormalizedEvent(
                type="message",
                stage=stage,
                text="Inspecting repository layout and existing tests.",
            )
        )
        add(
            NormalizedEvent(
                type="tool_call",
                stage=stage,
                name="shell",
                summary="ls -la",
                args={"cmd": "ls -la"},
            )
        )
        add(
            NormalizedEvent(
                type="tool_result",
                stage=stage,
                name="shell",
                result="LICENSE\nREADME.md\npyproject.toml\nsrc",
            )
        )
        inp, out = _usage(extra, prompt)
        add(
            NormalizedEvent(
                type="usage",
                stage=stage,
                input_tokens=inp,
                output_tokens=out,
                total_tokens=inp + out,
            )
        )
        if mode == "fail":
            add(
                NormalizedEvent(
                    type="error",
                    stage=stage,
                    message="planner reported repository is unbuildable",
                )
            )
            return events
        # Produce a structured plan in YAML.
        plan_yaml = _plan_for(prompt)
        add(
            NormalizedEvent(
                type="message",
                stage=stage,
                text="Drafting implementation plan.",
            )
        )
        add(
            NormalizedEvent(
                type="final",
                stage=stage,
                artifact="plan.md",
                extra={"plan_yaml": plan_yaml},
            )
        )
        return events

    # stage == implementation
    add(
        NormalizedEvent(
            type="message",
            stage=stage,
            text="Applying the approved plan.",
        )
    )
    add(
        NormalizedEvent(
            type="tool_call",
            stage=stage,
            name="edit",
            summary="apply plan step 1",
        )
    )
    add(
        NormalizedEvent(
            type="tool_result",
            stage=stage,
            name="edit",
            result="ok",
        )
    )
    if mode == "ask":
        add(
            NormalizedEvent(
                type="approval_request",
                stage=stage,
                reason="network",
            )
        )
        return events
    if mode == "amend":
        add(
            NormalizedEvent(
                type="message",
                stage=stage,
                text="Plan amendment proposed: add a test for the new branch.",
            )
        )
    if mode == "fail":
        add(
            NormalizedEvent(
                type="error",
                stage=stage,
                message="implementor could not apply plan",
            )
        )
        return events
    inp, out = _usage(extra, prompt, multiplier=2)
    add(
        NormalizedEvent(
            type="usage",
            stage=stage,
            input_tokens=inp,
            output_tokens=out,
            total_tokens=inp + out,
        )
    )
    add(
        NormalizedEvent(
            type="final",
            stage=stage,
            artifact="final.md",
            text="Implementation complete.",
        )
    )
    return events


def _usage(extra: dict[str, Any], prompt: str, *, multiplier: int = 1) -> tuple[int, int]:
    if extra.get("tokens_in") is not None and extra.get("tokens_out") is not None:
        return int(extra["tokens_in"]), int(extra["tokens_out"])
    base_in, base_out = _simulated_usage(prompt)
    return base_in * multiplier, base_out * multiplier


def _plan_for(prompt: str) -> str:
    """Produce a tiny YAML plan seeded from the prompt text."""
    seed = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:6]
    return (
        f"goal: \"Implement: {prompt[:60].strip().replace(chr(10), ' ')}\"\n"
        f"repo_seed: {seed}\n"
        "constraints:\n"
        "  - \"Keep public API stable\"\n"
        "plan:\n"
        "  - \"Inspect module\"\n"
        "  - \"Add change\"\n"
        "  - \"Add tests\"\n"
        "done_definition:\n"
        "  - \"Tests pass\"\n"
        "risk_notes: []\n"
    )


__all__ = ["FakeAdapter"]
