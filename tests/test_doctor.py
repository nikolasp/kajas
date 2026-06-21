"""Tests for the doctor module."""

from __future__ import annotations

from pathlib import Path

import pytest

from kajas import doctor
from kajas.config import GlobalConfig


def test_basic_checks_pass_on_minimal_config() -> None:
    cfg = GlobalConfig.model_validate({})
    results = doctor.run_basic_checks(cfg)
    assert any(r.name == "config:global" and r.ok for r in results)
    assert all(r.name != "auth" or r.ok for r in results)


def test_capability_gap_is_reported(tmp_path: Path) -> None:
    cfg = GlobalConfig.model_validate(
        {
            "adapters": {
                "pi": {
                    "command": "pi",
                    "mode": "json",
                    "supports": {
                        "sandbox": False,
                        "approval_policy": False,
                        "working_dir": True,
                        "network_gate": False,
                        "destructive_gate": False,
                    },
                }
            },
            "policies": {"careful": {"network": "ask", "destructive_command": "ask", "outside_workspace": "ask"}},
            "agents": {"p": {"tool": "pi", "policy": "careful"}},
            "approval_gate_sets": {"default": {}},
            "workflows": {
                "default": {"planner": "p", "implementor": "p", "approval_gate_set": "default"}
            },
        }
    )
    results = doctor.run_basic_checks(cfg)
    capability_check = next(r for r in results if r.name == "capability:default:planner")
    assert not capability_check.ok
    assert "network" in capability_check.detail


def test_project_checks_accept_config_project_paths_as_strings(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    cfg = GlobalConfig.model_validate(
        {"projects": [{"name": "project", "path": str(project)}]}
    )
    results = doctor.run_basic_checks(cfg)
    assert any(
        r.name == "project:project" and r.ok and r.detail == "registered, no .kajas/ yet"
        for r in results
    )
    assert any(r.name == "project:project:write" and r.ok for r in results)


def test_summarize_aggregates() -> None:
    summary = doctor.summarize(
        [
            doctor.CheckResult(name="a", ok=True, detail="ok"),
            doctor.CheckResult(name="b", ok=False, detail="broken"),
        ]
    )
    assert summary["ok"] is False
    assert len(summary["checks"]) == 2
