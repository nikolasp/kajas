"""Run model and orchestration.

A ``Run`` is the user-visible unit of work. Each run lives in its own
folder under ``<project>/.kajas/runs/<id>/`` and is described by a
``run.md`` frontmatter block. The orchestrator streams normalized
events into ``events.ndjson`` and keeps an in-memory view of the run
state for the API to query.

States (taken from the v1 design)::

    draft
    planning
    awaiting_plan_approval
    implementing
    verifying
    awaiting_final_acceptance
    completed
    failed
    cancelled
    interrupted

Transitions are driven by the orchestrator (the agent thread) and by
explicit user actions (``approve-plan``, ``cancel``). The state is
serialised to ``run.md`` on every change so the run folder is the
authoritative source of truth on restart.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .adapters.base import (
    Adapter,
    AdapterProcess,
    NormalizedEvent,
    Stage,
    load_registry,
)
from .config import (
    AgentProfile,
    GlobalConfig,
    PolicySpec,
    WorkflowSpec,
    capability_gaps,
    effective_policy,
    load_global_config,
    load_project_config_raw,
    merge_configs,
)


log = logging.getLogger("kajas.runs")

RUN_STATUSES = (
    "draft",
    "planning",
    "awaiting_plan_approval",
    "implementing",
    "verifying",
    "awaiting_final_acceptance",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "deleted",  # transient: API/UI only
)

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
IN_FLIGHT_STATUSES = (
    "planning",
    "awaiting_plan_approval",
    "implementing",
    "verifying",
    "awaiting_final_acceptance",
)


# ---------------------------------------------------------------------------
# ID and naming
# ---------------------------------------------------------------------------


_RUN_ID_INVALID = re.compile(r"[^a-zA-Z0-9_.-]")


def _sanitize_id_part(s: str) -> str:
    s = _RUN_ID_INVALID.sub("-", s.strip())
    return s.strip("-") or "run"


def make_run_id(title: str, *, when: dt.datetime | None = None) -> str:
    when = when or dt.datetime.now()
    stamp = when.strftime("%Y-%m-%d-%H%M%S")
    return f"{stamp}-{_sanitize_id_part(title)[:48]}"


# ---------------------------------------------------------------------------
# Per-run persisted frontmatter
# ---------------------------------------------------------------------------


class UsageBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @classmethod
    def from_event(cls, ev: NormalizedEvent) -> "UsageBlock":
        return cls(
            input_tokens=ev.input_tokens,
            output_tokens=ev.output_tokens,
            total_tokens=ev.total_tokens,
        )

    def add(self, other: "UsageBlock") -> "UsageBlock":
        return UsageBlock(
            input_tokens=_add(self.input_tokens, other.input_tokens),
            output_tokens=_add(self.output_tokens, other.output_tokens),
            total_tokens=_add(self.total_tokens, other.total_tokens),
        )


def _add(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


class RunRecord(BaseModel):
    """The serialisable run frontmatter persisted to ``run.md``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_path: str
    project_name: str
    status: str = "draft"
    workflow: str
    title: str = ""
    prompt: str = ""
    started_at: str = Field(
        default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds")
    )
    updated_at: str = Field(
        default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds")
    )
    usage: dict[str, UsageBlock | None] = Field(default_factory=dict)
    planner_agent: str | None = None
    implementor_agent: str | None = None
    plan_approved_at: str | None = None
    plan_amendments: list[dict[str, Any]] = Field(default_factory=list)
    final_summary: str | None = None
    effective_config: dict[str, Any] = Field(default_factory=dict)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None

    def touch(self) -> None:
        self.updated_at = dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# File layout helpers
# ---------------------------------------------------------------------------


def run_dir(project_path: Path, run_id: str) -> Path:
    from .run_store import DEFAULT_RUN_STORE

    return DEFAULT_RUN_STORE.run_dir(project_path, run_id)


def _write_run_md(record: RunRecord, body: str, path: Path) -> None:
    from .run_store import DEFAULT_RUN_STORE

    DEFAULT_RUN_STORE.write_run_md(record, body, path)


def _read_run_md(path: Path) -> tuple[RunRecord, str]:
    from .run_store import DEFAULT_RUN_STORE

    return DEFAULT_RUN_STORE.read_run_md(path)


# ---------------------------------------------------------------------------
# Live in-memory state (one per orchestrator instance)
# ---------------------------------------------------------------------------


class RunHandle:
    """In-memory state for a run. Lives only as long as the process."""

    def __init__(self, record: RunRecord, dir: Path) -> None:
        self.record = record
        self.dir = dir
        self._lock = threading.RLock()
        self.process: AdapterProcess | None = None
        self._listeners: list[asyncio.Queue[NormalizedEvent]] = []
        self._final_event: NormalizedEvent | None = None
        self._cancelled = threading.Event()
        # ``_plan_approved`` is set by :meth:`Orchestrator.approve_plan`
        # to unblock the agent thread that is paused at the
        # ``awaiting_plan_approval`` gate.
        self._plan_approved = threading.Event()
        self._approved_plan: str | None = None

    # ---- listeners -------------------------------------------------------

    def attach(self) -> asyncio.Queue[NormalizedEvent]:
        q: asyncio.Queue[NormalizedEvent] = asyncio.Queue(maxsize=1024)
        self._listeners.append(q)
        return q

    def detach(self, q: asyncio.Queue[NormalizedEvent]) -> None:
        try:
            self._listeners.remove(q)
        except ValueError:
            pass

    def _broadcast(self, ev: NormalizedEvent) -> None:
        # Each listener has a bounded queue; if a slow consumer falls
        # behind we drop the oldest event rather than block the agent.
        for q in self._listeners:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    pass

    # ---- cancellation ----------------------------------------------------

    def request_cancel(self) -> None:
        self._cancelled.set()
        # Unblock any waiters (e.g. the plan-approval gate) so the
        # agent thread can notice the cancel and exit cleanly.
        self._plan_approved.set()
        with self._lock:
            proc = self.process
        if proc is not None:
            proc.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class _AdapterRequest:
    stage: Stage
    agent_name: str
    prompt_path: Path


class Orchestrator:
    """Owns the in-memory run table and runs the agent loop in a thread."""

    def __init__(self) -> None:
        self._handles: dict[str, RunHandle] = {}
        self._lock = threading.RLock()

    # ---- handle registry -------------------------------------------------

    def _register(self, handle: RunHandle) -> None:
        with self._lock:
            self._handles[handle.record.id] = handle

    def _drop(self, run_id: str) -> None:
        with self._lock:
            self._handles.pop(run_id, None)

    def get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._handles.get(run_id)

    def all_active(self) -> list[RunHandle]:
        with self._lock:
            return list(self._handles.values())

    # ---- run creation ----------------------------------------------------

    def create_run(
        self,
        *,
        project_name: str,
        project_path: Path,
        workflow_name: str,
        title: str,
        prompt: str,
        global_config: GlobalConfig | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> RunHandle:
        """Create a new run folder and a :class:`RunHandle`.

        This is synchronous: it sets up files on disk and registers the
        handle. The actual agent loop is started by :meth:`start`.
        """
        global_config = global_config or load_global_config()
        project_cfg = load_project_config_raw(project_path) if (project_path / ".kajas").exists() else None
        merged = merge_configs(global_config, project_cfg)

        workflow = merged.workflows.get(workflow_name)
        if workflow is None:
            raise KeyError(f"workflow {workflow_name!r} is not defined in the merged config")
        planner = merged.agents.get(workflow.planner)
        implementor = merged.agents.get(workflow.implementor)
        if planner is None or implementor is None:
            raise KeyError("workflow references missing agents")

        record = RunRecord(
            id=make_run_id(title or prompt[:32]),
            project_path=str(project_path),
            project_name=project_name,
            status="draft",
            workflow=workflow_name,
            title=title or prompt[:80].strip(),
            prompt=prompt,
            planner_agent=workflow.planner,
            implementor_agent=workflow.implementor,
            usage={"planning": None, "implementation": None, "verification": None},
            effective_config=merged.model_dump(mode="json"),
        )
        # Apply per-run overrides onto the snapshot. ``overrides`` can be
        # used by the New Run screen to flip individual policy fields
        # without mutating any persisted config.
        if overrides:
            _apply_overrides(record.effective_config, overrides)

        from .run_store import DEFAULT_RUN_STORE

        rdir = DEFAULT_RUN_STORE.create_run(
            project_path,
            record,
            planner_prompt=_render_planner_prompt(
                record, merged, workflow, planner, project_path
            ),
        )

        handle = RunHandle(record=record, dir=rdir)
        self._register(handle)
        return handle

    # ---- lifecycle -------------------------------------------------------

    def start(self, handle: RunHandle) -> None:
        """Spawn the agent thread for ``handle``."""
        if handle.record.status not in ("draft", "interrupted"):
            raise RuntimeError(
                f"cannot start run in status {handle.record.status!r}"
            )
        thread = threading.Thread(
            target=self._run, args=(handle,), name=f"kajas-run-{handle.record.id}"
        )
        thread.daemon = True
        thread.start()

    def rerun_failed_phase(
        self,
        *,
        project_path: Path,
        run_id: str,
        global_config: GlobalConfig | None = None,
    ) -> RunHandle:
        handle = self.get(run_id)
        if handle is None:
            record = load_run(project_path, run_id)
            if record is None:
                raise FileNotFoundError(f"run {run_id!r} not found")
            handle = RunHandle(record=record, dir=run_dir(project_path, run_id))
            self._register(handle)

        if handle.record.status != "failed":
            raise RuntimeError(
                f"cannot rerun failed phase in status {handle.record.status!r}"
            )

        merged = _current_merged_config_for_run(handle.record, global_config)
        handle.record.effective_config = merged.model_dump(mode="json")
        handle.record.error = None
        handle.record.final_summary = None

        if handle.record.plan_approved_at:
            stage = "implementation"
            handle.record.usage["implementation"] = None
            handle.record.usage["verification"] = None
            _materialise_implementor_prompt(
                handle,
                merged,
                merged.agents[handle.record.implementor_agent],
                Path(handle.record.project_path),
            )
            target = self._rerun_implementation
        else:
            stage = "planning"
            handle.record.usage = {
                "planning": None,
                "implementation": None,
                "verification": None,
            }
            handle.record.plan_approved_at = None
            handle.record.approvals = [
                a for a in handle.record.approvals if a.get("kind") != "plan"
            ]
            _materialise_planner_prompt(
                handle,
                merged,
                merged.agents[handle.record.planner_agent],
                Path(handle.record.project_path),
            )
            target = self._run

        handle.record.touch()
        _persist(handle)
        _emit(
            handle,
            NormalizedEvent(
                type="log",
                stage=stage,
                text=f"rerun {stage} requested",
            ),
        )

        thread = threading.Thread(
            target=target, args=(handle,), name=f"kajas-rerun-{handle.record.id}"
        )
        thread.daemon = True
        thread.start()
        return handle

    def approve_plan(self, handle: RunHandle, *, edited_plan: str | None) -> None:
        """Mark the plan as approved and (optionally) replace ``plan.approved.md``."""
        if handle.record.status != "awaiting_plan_approval":
            raise RuntimeError(
                f"cannot approve plan in status {handle.record.status!r}"
            )
        with handle._lock:
            from .run_store import DEFAULT_RUN_STORE

            if edited_plan is not None:
                DEFAULT_RUN_STORE.write_text(
                    handle.dir, DEFAULT_RUN_STORE.approved_plan_file, edited_plan
                )
                handle._approved_plan = edited_plan
            handle.record.plan_approved_at = dt.datetime.now().isoformat(timespec="seconds")
            handle.record.approvals.append(
                {
                    "kind": "plan",
                    "at": handle.record.plan_approved_at,
                    "edited": edited_plan is not None,
                }
            )
            handle.record.status = "implementing"
            handle.record.touch()
            DEFAULT_RUN_STORE.save_record(handle.dir, handle.record)
        # Unblock the agent thread that is paused at the gate.
        handle._plan_approved.set()

    def cancel(self, run_id: str) -> None:
        handle = self.get(run_id)
        if handle is None:
            return
        handle.request_cancel()
        with handle._lock:
            if handle.record.status in TERMINAL_STATUSES:
                return
            stage: Stage = (
                "implementation"
                if handle.record.status
                in {"implementing", "verifying", "awaiting_final_acceptance"}
                else "planning"
            )
            handle.record.status = "cancelled"
            handle.record.touch()
            _persist_and_emit_status(handle, stage)

    def delete(self, run_id: str) -> None:
        handle = self.get(run_id)
        if handle is None:
            return
        if not handle.record.status in TERMINAL_STATUSES + ("interrupted", "draft"):
            raise RuntimeError(
                f"cannot delete run in status {handle.record.status!r}"
            )
        from .run_store import DEFAULT_RUN_STORE

        DEFAULT_RUN_STORE.delete(Path(handle.record.project_path), handle.record.id)
        self._drop(run_id)

    # ---- agent thread ----------------------------------------------------

    def _run(self, handle: RunHandle) -> None:
        try:
            merged = GlobalConfig.model_validate(handle.record.effective_config)
            workflow = merged.workflows[handle.record.workflow]
            gates = merged.approval_gate_sets[workflow.approval_gate_set]
            planner = merged.agents[handle.record.planner_agent]
            implementor = merged.agents[handle.record.implementor_agent]
            project_path = Path(handle.record.project_path)
            adapters = load_registry([planner.tool, implementor.tool])
            policy = _resolve_run_policy(merged, planner, implementor)
            if policy is None:
                handle.record.status = "failed"
                handle.record.error = (
                    "Policy not enforceable by selected tools. "
                    "Set allow_unenforced_policy: true to override."
                )
                handle.record.touch()
                _persist(handle)
                return

            # ---- Planning ------------------------------------------------
            handle.record.status = "planning"
            handle.record.touch()
            _persist_and_emit_status(handle, "planning")
            planner_ok = self._run_stage(
                handle,
                stage="planning",
                profile=planner,
                adapter=adapters.get(planner.tool),
                merged=merged,
                policy=policy,
                project_path=project_path,
            )
            if not planner_ok:
                if handle.cancelled:
                    handle.record.status = "cancelled"
                else:
                    handle.record.status = "failed"
                handle.record.touch()
                _persist(handle)
                return

            # Pause for plan approval if configured.
            if gates.pause_before_implementation:
                handle.record.status = "awaiting_plan_approval"
                handle.record.touch()
                _persist_and_emit_status(handle, "planning")
                # Block until the user approves (or cancels).
                handle._plan_approved.wait()
                if handle.cancelled:
                    handle.record.status = "cancelled"
                    handle.record.touch()
                    _persist_and_emit_status(handle, "planning")
                    return
                if handle.record.status != "implementing":
                    # Approval handler already advanced the state.
                    return

            # ---- Implementation -----------------------------------------
            self._implement(handle, workflow, gates, merged, implementor, adapters, policy, project_path)
        except Exception:  # pragma: no cover - defensive
            log.exception("run %s crashed", handle.record.id)
            handle.record.status = "failed"
            handle.record.error = "orchestrator crashed (see server logs)"
            handle.record.touch()
            _persist(handle)

    def _rerun_implementation(self, handle: RunHandle) -> None:
        try:
            merged = GlobalConfig.model_validate(handle.record.effective_config)
            workflow = merged.workflows[handle.record.workflow]
            gates = merged.approval_gate_sets[workflow.approval_gate_set]
            planner = merged.agents[handle.record.planner_agent]
            implementor = merged.agents[handle.record.implementor_agent]
            project_path = Path(handle.record.project_path)
            adapters = load_registry([implementor.tool])
            policy = _resolve_run_policy(merged, planner, implementor)
            if policy is None:
                handle.record.status = "failed"
                handle.record.error = (
                    "Policy not enforceable by selected tools. "
                    "Set allow_unenforced_policy: true to override."
                )
                handle.record.touch()
                _persist(handle)
                return
            self._implement(
                handle,
                workflow,
                gates,
                merged,
                implementor,
                adapters,
                policy,
                project_path,
            )
        except Exception:  # pragma: no cover - defensive
            log.exception("run %s implementation rerun crashed", handle.record.id)
            handle.record.status = "failed"
            handle.record.error = "orchestrator crashed (see server logs)"
            handle.record.touch()
            _persist(handle)

    def _implement(
        self,
        handle: RunHandle,
        workflow: WorkflowSpec,
        gates,
        merged: GlobalConfig,
        implementor: AgentProfile,
        adapters: dict[str, Adapter],
        policy: PolicySpec,
        project_path: Path,
    ) -> None:
        handle.record.status = "implementing"
        handle.record.touch()
        _persist_and_emit_status(handle, "implementation")
        implementor_ok = self._run_stage(
            handle,
            stage="implementation",
            profile=implementor,
            adapter=adapters.get(implementor.tool),
            merged=merged,
            policy=policy,
            project_path=project_path,
        )
        if not implementor_ok:
            if handle.cancelled:
                handle.record.status = "cancelled"
            else:
                handle.record.status = "failed"
            handle.record.touch()
            _persist(handle)
            return
        # Verification
        handle.record.status = "verifying"
        handle.record.touch()
        _persist_and_emit_status(handle, "implementation")
        _run_verification(handle, workflow, project_path)
        if gates.pause_final_acceptance:
            handle.record.status = "awaiting_final_acceptance"
        else:
            handle.record.status = "completed"
        handle.record.touch()
        _persist_and_emit_status(handle, "implementation")

    def _run_stage(
        self,
        handle: RunHandle,
        *,
        stage: Stage,
        profile: AgentProfile,
        adapter: Adapter | None,
        merged: GlobalConfig,
        policy: PolicySpec,
        project_path: Path,
    ) -> bool:
        if adapter is None:
            _emit(
                handle,
                NormalizedEvent(
                    type="error",
                    stage=stage,
                    message=f"no adapter registered for tool {profile.tool!r}",
                ),
            )
            return False
        prompt_path = handle.dir / "prompts" / f"{stage}.md"
        # ``stage`` is ``planning`` or ``implementation``. We use the
        # friendlier filenames ``planner.md`` and ``implementor.md`` for
        # the materialised prompts, so rewrite here.
        prompt_path = handle.dir / "prompts" / ("planner.md" if stage == "planning" else "implementor.md")
        if not prompt_path.exists():
            if stage == "planning":
                _materialise_planner_prompt(handle, merged, profile, project_path)
            else:
                _materialise_implementor_prompt(handle, merged, profile, project_path)
        env = _build_env(merged, profile)
        proc = adapter.start(
            stage=stage,
            run_id=handle.record.id,
            project_path=project_path,
            profile_model=profile.model,
            prompt_path=prompt_path,
            raw_dir=handle.dir / "raw",
            env=env,
        )
        with handle._lock:
            handle.process = proc
        if proc.events is None:
            return True
        try:
            for ev in proc.events:
                if handle.cancelled:
                    proc.cancel()
                    return False
                _emit(handle, ev)
                _record_usage(handle, ev)
                if ev.type == "final" and ev.artifact == "plan.md":
                    _write_plan(handle, ev)
                if ev.type == "error":
                    handle.record.error = ev.message or ev.text or "adapter error"
                    handle.record.touch()
                    return False
            return True
        finally:
            with handle._lock:
                handle.process = None


# ---------------------------------------------------------------------------
# Event + persistence helpers
# ---------------------------------------------------------------------------


def _emit(handle: RunHandle, ev: NormalizedEvent) -> None:
    from .run_store import DEFAULT_RUN_STORE

    DEFAULT_RUN_STORE.append_event(handle.dir, ev)
    handle._broadcast(ev)


def _record_usage(handle: RunHandle, ev: NormalizedEvent) -> None:
    if ev.type != "usage":
        return
    block = UsageBlock.from_event(ev)
    stage = ev.stage
    current = handle.record.usage.get(stage)
    if current is None:
        handle.record.usage[stage] = block
    else:
        handle.record.usage[stage] = current.add(block)
    handle.record.touch()


def _persist(handle: RunHandle) -> None:
    from .run_store import DEFAULT_RUN_STORE

    DEFAULT_RUN_STORE.save_record(handle.dir, handle.record)


def _persist_and_emit_status(handle: RunHandle, stage: Stage) -> None:
    _persist(handle)
    _emit(
        handle,
        NormalizedEvent(
            type="status",
            stage=stage,
            status=handle.record.status,
            text=handle.record.status,
        ),
    )


def _write_plan(handle: RunHandle, ev: NormalizedEvent) -> None:
    if ev.type != "final" or ev.artifact != "plan.md":
        return
    plan_yaml = (ev.extra or {}).get("plan_yaml")
    from .run_store import DEFAULT_RUN_STORE

    if plan_yaml:
        DEFAULT_RUN_STORE.write_text(handle.dir, DEFAULT_RUN_STORE.plan_file, plan_yaml)
    else:
        # Fall back to whatever the agent emitted as its final message.
        text = ev.text or "(no plan content)"
        DEFAULT_RUN_STORE.write_text(
            handle.dir, DEFAULT_RUN_STORE.plan_file, f"# Plan\n\n{text}\n"
        )


def _render_run_body(record: RunRecord) -> str:
    from .run_store import DEFAULT_RUN_STORE

    return DEFAULT_RUN_STORE.render_run_body(record)


def _render_planner_prompt(
    record: RunRecord,
    merged: GlobalConfig,
    workflow: WorkflowSpec,
    profile: AgentProfile,
    project_path: Path,
) -> str:
    policy = effective_policy(merged, profile)
    return (
        f"# Planner Brief for Run {record.id}\n\n"
        f"## Project\n\n`{project_path}`\n\n"
        f"## Workflow\n\n`{record.workflow}`\n\n"
        f"## Effective Policy\n\n"
        f"- network: `{policy.network}`\n"
        f"- destructive_command: `{policy.destructive_command}`\n"
        f"- outside_workspace: `{policy.outside_workspace}`\n\n"
        f"## User Prompt\n\n{record.prompt}\n\n"
        f"## Required Output\n\n"
        f"Produce a single YAML document with the following shape:\n\n"
        f"```yaml\n"
        f"goal: <one-line goal>\n"
        f"repo: <absolute path>\n"
        f"constraints:\n  - <string>\n"
        f"plan:\n  - <step>\n"
        f"done_definition:\n  - <verifiable statement>\n"
        f"risk_notes:\n  - <string>\n"
        f"```\n\n"
        f"Emit a final `final` event with `artifact=plan.md` and the YAML\n"
        f"in `extra.plan_yaml`.\n"
    )


def _materialise_planner_prompt(
    handle: RunHandle, merged: GlobalConfig, profile: AgentProfile, project_path: Path
) -> None:
    workflow = merged.workflows[handle.record.workflow]
    text = _render_planner_prompt(handle.record, merged, workflow, profile, project_path)
    from .run_store import DEFAULT_RUN_STORE

    DEFAULT_RUN_STORE.write_text(handle.dir, "prompts/planner.md", text)


def _materialise_implementor_prompt(
    handle: RunHandle, merged: GlobalConfig, profile: AgentProfile, project_path: Path
) -> None:
    policy = effective_policy(merged, profile)
    from .run_store import DEFAULT_RUN_STORE

    plan_text = DEFAULT_RUN_STORE.read_text(
        handle.dir, DEFAULT_RUN_STORE.approved_plan_file
    ) or ""
    text = (
        f"# Implementor Brief for Run {handle.record.id}\n\n"
        f"## Project\n\n`{project_path}`\n\n"
        f"## Effective Policy\n\n"
        f"- network: `{policy.network}`\n"
        f"- destructive_command: `{policy.destructive_command}`\n"
        f"- outside_workspace: `{policy.outside_workspace}`\n\n"
        f"## Approved Plan\n\n{plan_text}\n\n"
        f"## Your Job\n\n"
        f"Apply the approved plan to the repository under the policy above.\n"
        f"When done, emit a final event with `artifact=final.md` and a\n"
        f"short summary in `text`.\n"
    )
    DEFAULT_RUN_STORE.write_text(handle.dir, "prompts/implementor.md", text)


def _resolve_run_policy(
    merged: GlobalConfig, planner: AgentProfile, implementor: AgentProfile
) -> PolicySpec | None:
    """Return the most restrictive effective policy, or ``None`` if the
    selected tools cannot enforce it and the run does not explicitly
    allow running with unenforced policy."""
    pp = effective_policy(merged, planner)
    ip = effective_policy(merged, implementor)
    combined = PolicySpec(
        network=_most_restrictive(pp.network, ip.network),
        destructive_command=_most_restrictive(pp.destructive_command, ip.destructive_command),
        outside_workspace=_most_restrictive(pp.outside_workspace, ip.outside_workspace),
        allow_unenforced_policy=pp.allow_unenforced_policy or ip.allow_unenforced_policy,
    )
    # Check capability gaps for the stricter of the two profiles.
    chosen = planner if _strictness(pp) >= _strictness(ip) else implementor
    gaps = capability_gaps(merged, chosen, combined)
    if gaps and not combined.allow_unenforced_policy and not (
        planner.allow_unenforced_policy or implementor.allow_unenforced_policy
    ):
        return None
    return combined


def _most_restrictive(a: str, b: str) -> str:
    order = {"allow": 0, "ask": 1, "deny": 2}
    return a if order[a] >= order[b] else b


def _strictness(p: PolicySpec) -> int:
    order = {"allow": 0, "ask": 1, "deny": 2}
    return max(order[p.network], order[p.destructive_command], order[p.outside_workspace])


def _build_env(merged: GlobalConfig, profile: AgentProfile) -> dict[str, str]:
    """Resolve ``env:NAME`` style references against the process env."""
    out: dict[str, str] = {}
    tool = merged.tools.get(profile.tool)
    if tool is not None:
        for k, v in tool.env.items():
            out[k] = _resolve_env_value(v)
    adapter = merged.adapters.get(profile.tool)
    if adapter is not None:
        for k, v in adapter.env.items():
            out[k] = _resolve_env_value(v)
    return out


def _resolve_env_value(v: str) -> str:
    if v.startswith("env:"):
        name = v[4:]
        return os.environ.get(name, "")
    return v


def _apply_overrides(snapshot: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Apply a per-run override blob onto the config snapshot."""
    for key, value in overrides.items():
        if value is None:
            continue
        if key in snapshot and isinstance(snapshot[key], dict) and isinstance(value, dict):
            snapshot[key].update(value)
        else:
            snapshot[key] = value


def _current_merged_config_for_run(
    record: RunRecord, global_config: GlobalConfig | None = None
) -> GlobalConfig:
    project_path = Path(record.project_path)
    global_config = global_config or load_global_config()
    project_cfg = load_project_config_raw(project_path) if (project_path / ".kajas").exists() else None
    merged = merge_configs(global_config, project_cfg)
    workflow = merged.workflows.get(record.workflow)
    if workflow is None:
        raise RuntimeError(f"workflow {record.workflow!r} is not defined")
    if workflow.planner not in merged.agents:
        raise RuntimeError(f"planner agent {workflow.planner!r} is not defined")
    if workflow.implementor not in merged.agents:
        raise RuntimeError(f"implementor agent {workflow.implementor!r} is not defined")
    record.planner_agent = workflow.planner
    record.implementor_agent = workflow.implementor
    return merged


def _run_verification(handle: RunHandle, workflow: WorkflowSpec, project_path: Path) -> None:
    import subprocess

    if not workflow.verification.commands:
        handle.record.final_summary = "No verification commands configured."
        return
    results: list[str] = []
    for cmd in workflow.verification.commands:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=project_path, capture_output=True, text=True, timeout=600
            )
            results.append(f"## `{cmd}` (exit={proc.returncode})\n\n```\n{proc.stdout[-2000:]}\n{proc.stderr[-500:]}\n```")
        except Exception as exc:  # noqa: BLE001
            results.append(f"## `{cmd}` (error)\n\n```\n{exc}\n```")
    from .run_store import DEFAULT_RUN_STORE

    DEFAULT_RUN_STORE.write_text(handle.dir, "verification.md", "\n\n".join(results))
    handle.record.final_summary = (
        DEFAULT_RUN_STORE.read_text(handle.dir, "verification.md") or ""
    )


# ---------------------------------------------------------------------------
# Discovery (used by the API at startup)
# ---------------------------------------------------------------------------


def discover_runs(project_path: Path) -> list[RunRecord]:
    from .run_store import DEFAULT_RUN_STORE

    return DEFAULT_RUN_STORE.discover(project_path)


def load_run(project_path: Path, run_id: str) -> RunRecord | None:
    from .run_store import DEFAULT_RUN_STORE

    return DEFAULT_RUN_STORE.read_record(project_path, run_id)


def mark_interrupted_on_startup(project_path: Path) -> list[str]:
    """Mark any in-flight run as ``interrupted`` so the UI can offer rerun/delete."""
    from .run_store import DEFAULT_RUN_STORE

    return DEFAULT_RUN_STORE.mark_interrupted_on_startup(project_path)


__all__ = [
    "Orchestrator",
    "RunHandle",
    "RunRecord",
    "TERMINAL_STATUSES",
    "IN_FLIGHT_STATUSES",
    "RUN_STATUSES",
    "UsageBlock",
    "delete_run_dir",
    "discover_runs",
    "load_run",
    "make_run_id",
    "mark_interrupted_on_startup",
    "run_dir",
]


def delete_run_dir(project_path: Path, run_id: str) -> None:
    from .run_store import DEFAULT_RUN_STORE

    DEFAULT_RUN_STORE.delete(project_path, run_id)
