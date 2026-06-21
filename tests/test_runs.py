"""Tests for the run orchestrator, using the fake adapter."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kajas.config import (
    AdapterSpec,
    AgentProfile,
    ApprovalGateSet,
    GlobalConfig,
    PolicySpec,
    WorkflowSpec,
    VerificationSpec,
)
from kajas.runs import Orchestrator, RunRecord, make_run_id, run_dir


def _build_global_config() -> GlobalConfig:
    return GlobalConfig.model_validate(
        {
            "adapters": {
                "fake": {
                    "command": "fake",
                    "mode": "json",
                    "supports": {
                        "sandbox": True,
                        "approval_policy": True,
                        "working_dir": True,
                        "network_gate": True,
                        "destructive_gate": True,
                    },
                }
            },
            "policies": {
                "careful": {
                    "network": "ask",
                    "destructive_command": "ask",
                    "outside_workspace": "ask",
                    "allow_unenforced_policy": True,
                }
            },
            "agents": {
                "planner": {"tool": "fake", "model": "default", "role": "planner", "policy": "careful"},
                "implementor": {"tool": "fake", "model": "default", "role": "implementor", "policy": "careful"},
            },
            "approval_gate_sets": {
                "default": {
                    "pause_before_implementation": True,
                    "pause_amendment": False,
                    "pause_final_acceptance": False,
                }
            },
            "workflows": {
                "default": {
                    "planner": "planner",
                    "implementor": "implementor",
                    "approval_gate_set": "default",
                    "verification": {"commands": [], "require_final_summary": True},
                }
            },
        }
    )


def _wait_for(handle, statuses, *, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if handle.record.status in statuses:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"run {handle.record.id} stuck in {handle.record.status!r}, "
        f"wanted one of {statuses}"
    )


def test_run_id_is_deterministic_shape(tmp_path: Path) -> None:
    rid = make_run_id("Add OAuth Login", when=__import__("datetime").datetime(2026, 6, 21, 14, 30, 12))
    assert rid == "2026-06-21-143012-Add-OAuth-Login"


def test_happy_path_runs_to_completion(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    orch = Orchestrator()
    handle = orch.create_run(
        project_name="proj",
        project_path=project_path,
        workflow_name="default",
        title="hello",
        prompt="<!-- kajas:fake mode=happy -->\nSay hi",
        global_config=_build_global_config(),
    )
    orch.start(handle)
    _wait_for(handle, {"awaiting_plan_approval"})
    events_path = run_dir(project_path, handle.record.id) / "events.ndjson"
    assert '"type":"status"' in events_path.read_text(encoding="utf-8")
    assert '"status":"awaiting_plan_approval"' in events_path.read_text(encoding="utf-8")
    orch.approve_plan(handle, edited_plan=None)
    _wait_for(handle, {"completed"})
    # Files on disk
    assert (run_dir(project_path, handle.record.id) / "plan.md").exists()
    assert (run_dir(project_path, handle.record.id) / "plan.approved.md").exists()
    assert events_path.exists()
    # Usage populated
    assert handle.record.usage["planning"] is not None
    assert handle.record.usage["implementation"] is not None


def test_rerun_failed_implementation_phase(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    orch = Orchestrator()
    handle = orch.create_run(
        project_name="proj",
        project_path=project_path,
        workflow_name="default",
        title="rerun",
        prompt="<!-- kajas:fake mode=happy -->\nSay hi",
        global_config=_build_global_config(),
    )
    orch.start(handle)
    _wait_for(handle, {"awaiting_plan_approval"})
    orch.approve_plan(
        handle,
        edited_plan="<!-- kajas:fake mode=fail -->\nApply a failing plan.",
    )
    _wait_for(handle, {"failed"})
    assert handle.record.error == "implementor could not apply plan"

    approved_plan = run_dir(project_path, handle.record.id) / "plan.approved.md"
    approved_plan.write_text("Apply the original plan.", encoding="utf-8")
    rerun = orch.rerun_failed_phase(
        project_path=project_path,
        run_id=handle.record.id,
        global_config=_build_global_config(),
    )
    assert rerun is handle
    _wait_for(handle, {"completed"})
    events = (run_dir(project_path, handle.record.id) / "events.ndjson").read_text(
        encoding="utf-8"
    )
    assert "rerun implementation requested" in events
    assert handle.record.error is None
    assert handle.record.usage["implementation"] is not None


def test_cancel_during_planning(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    orch = Orchestrator()
    # Add a delay so we have time to cancel.
    handle = orch.create_run(
        project_name="proj",
        project_path=project_path,
        workflow_name="default",
        title="cancel",
        prompt="<!-- kajas:fake mode=happy delay=0.5 -->\nDo it",
        global_config=_build_global_config(),
    )
    orch.start(handle)
    _wait_for(handle, {"planning"})
    orch.cancel(handle.record.id)
    _wait_for(handle, {"cancelled", "failed", "interrupted"}, timeout=10.0)
    # Either cancelled or interrupted is acceptable; we just need the
    # state to have settled and not be in planning.
    assert handle.record.status not in ("planning", "implementing", "draft")


def test_planner_failure_marks_run_failed(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    orch = Orchestrator()
    handle = orch.create_run(
        project_name="proj",
        project_path=project_path,
        workflow_name="default",
        title="fail",
        prompt="<!-- kajas:fake mode=fail -->\nbreak it",
        global_config=_build_global_config(),
    )
    orch.start(handle)
    _wait_for(handle, {"failed"})
    assert handle.record.status == "failed"
    assert handle.record.error == "planner reported repository is unbuildable"
