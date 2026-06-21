"""Tests for the restart-as-interrupted behaviour."""

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
from kajas.runs import (
    IN_FLIGHT_STATUSES,
    Orchestrator,
    make_run_id,
    mark_interrupted_on_startup,
    run_dir,
)


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
            "policies": {"careful": {"network": "ask", "destructive_command": "ask", "outside_workspace": "ask", "allow_unenforced_policy": True}},
            "agents": {
                "planner": {"tool": "fake", "policy": "careful", "role": "planner"},
                "implementor": {"tool": "fake", "policy": "careful", "role": "implementor"},
            },
            "approval_gate_sets": {"default": {"pause_before_implementation": True, "pause_amendment": False, "pause_final_acceptance": False}},
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


def test_in_flight_runs_become_interrupted_on_restart(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    orch = Orchestrator()
    handle = orch.create_run(
        project_name="proj",
        project_path=project_path,
        workflow_name="default",
        title="restart",
        prompt="<!-- kajas:fake mode=happy -->\ndo",
        global_config=_build_global_config(),
    )
    # Don't actually start the run; just write a record in
    # ``implementing`` so the restart sweep finds it.
    handle.record.status = "implementing"
    from kajas.runs import _write_run_md, _render_run_body

    _write_run_md(handle.record, _render_run_body(handle.record), handle.dir / "run.md")

    interrupted = mark_interrupted_on_startup(project_path)
    assert handle.record.id in interrupted

    from kajas.runs import load_run
    reloaded = load_run(project_path, handle.record.id)
    assert reloaded is not None
    assert reloaded.status == "interrupted"
    assert reloaded.error is not None


def test_terminal_runs_are_not_touched(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    orch = Orchestrator()
    handle = orch.create_run(
        project_name="proj",
        project_path=project_path,
        workflow_name="default",
        title="done",
        prompt="<!-- kajas:fake mode=happy -->\ndo",
        global_config=_build_global_config(),
    )
    handle.record.status = "completed"
    from kajas.runs import _write_run_md, _render_run_body

    _write_run_md(handle.record, _render_run_body(handle.record), handle.dir / "run.md")

    interrupted = mark_interrupted_on_startup(project_path)
    assert interrupted == []
    from kajas.runs import load_run
    reloaded = load_run(project_path, handle.record.id)
    assert reloaded.status == "completed"
