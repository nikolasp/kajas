"""Tests for the config module: parsing, merging, cross-reference checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from kajas import config
from kajas.cli import default_global_config


def test_default_global_config_parses() -> None:
    cfg = config.GlobalConfig.model_validate({})
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8765
    assert cfg.auth.enabled is False


def test_starter_global_config_includes_real_agents() -> None:
    cfg = default_global_config()
    assert cfg.agents["planner"].tool == "codex"
    assert cfg.agents["planner"].model == "gpt-5.5"
    assert cfg.agents["planner"].role == "planner"
    assert cfg.agents["coder"].tool == "pi"
    assert (
        cfg.agents["coder"].model
        == "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf"
    )
    assert cfg.agents["coder"].role == "implementor"
    assert (
        cfg.agents["coder"].extra["local_model"]
        == "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf"
    )
    assert cfg.workflows["default"].planner == "planner"
    assert cfg.workflows["default"].implementor == "coder"
    assert config.validate_for_runtime(cfg) == []


def test_empty_runtime_config_is_seeded_with_default_agents() -> None:
    from kajas.server import _seed_runtime_defaults_if_empty

    empty = config.GlobalConfig.model_validate(
        {
            "auth": {
                "enabled": True,
                "passphrase_hash": "hash",
                "session_secret": "secret",
            },
            "projects": [{"name": "app", "path": "/tmp/app"}],
        }
    )
    cfg = _seed_runtime_defaults_if_empty(empty)
    assert cfg.auth.passphrase_hash == "hash"
    assert cfg.projects[0].name == "app"
    assert cfg.agents["planner"].tool == "codex"
    assert cfg.agents["planner"].model == "gpt-5.5"
    assert cfg.agents["coder"].tool == "pi"
    assert (
        cfg.agents["coder"].model
        == "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf"
    )
    assert (
        cfg.agents["coder"].extra["local_model"]
        == "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf"
    )


def test_auth_enabled_requires_hash() -> None:
    with pytest.raises(Exception):
        config.AuthConfig(enabled=True)


def test_deep_merge() -> None:
    base = {"a": 1, "b": {"c": 2, "d": 3}, "lst": [1, 2, 3]}
    over = {"a": 9, "b": {"d": 4, "e": 5}, "lst": [9]}
    merged = config._deep_merge(base, over)
    assert merged == {"a": 9, "b": {"c": 2, "d": 4, "e": 5}, "lst": [9]}


def test_deep_merge_none_in_override_keeps_base() -> None:
    base = {"a": 1, "b": 2}
    over = {"a": None}
    assert config._deep_merge(base, over) == {"a": 1, "b": 2}


def test_project_config_does_not_allow_projects_field() -> None:
    with pytest.raises(Exception):
        config.ProjectConfig.model_validate({"projects": [{"name": "x", "path": "/tmp"}]})


def test_load_and_merge(tmp_path: Path) -> None:
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        "server:\n  host: 127.0.0.1\n  port: 9000\nagents:\n  foo:\n    tool: codex\n",
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".kajas").mkdir()
    (project / ".kajas" / "config.yaml").write_text(
        "agents:\n  foo:\n    model: gpt-5\n",
        encoding="utf-8",
    )
    global_cfg = config.load_global_config(global_path)
    project_cfg = config.load_project_config_raw(project)
    merged = config.merge_configs(global_cfg, project_cfg)
    assert merged.agents["foo"].model == "gpt-5"
    assert merged.server.port == 9000


def test_validate_for_runtime() -> None:
    cfg = config.GlobalConfig.model_validate(
        {
            "tools": {"codex": {"command": "codex", "mode": "json"}},
            "agents": {
                "p": {"tool": "codex", "policy": "careful"},
            },
            "policies": {
                "careful": {"network": "ask", "destructive_command": "ask", "outside_workspace": "ask"},
            },
            "workflows": {
                "default": {"planner": "p", "implementor": "p", "approval_gate_set": "default"},
            },
            "approval_gate_sets": {"default": {}},
        }
    )
    assert config.validate_for_runtime(cfg) == []


def test_validate_for_runtime_detects_missing_agent() -> None:
    cfg = config.GlobalConfig.model_validate(
        {
            "workflows": {
                "default": {
                    "planner": "ghost",
                    "implementor": "ghost2",
                    "approval_gate_set": "default",
                }
            },
            "approval_gate_sets": {"default": {}},
        }
    )
    errors = config.validate_for_runtime(cfg)
    assert any("ghost" in e for e in errors)
    assert any("ghost2" in e for e in errors)


def test_capability_gaps_with_unenforced_tool() -> None:
    cfg = config.GlobalConfig.model_validate(
        {
            "adapters": {
                "pi": {
                    "command": "pi",
                    "mode": "json",
                    "supports": {"sandbox": False, "approval_policy": False, "working_dir": True, "network_gate": False, "destructive_gate": False},
                }
            },
            "policies": {"careful": {"network": "ask", "destructive_command": "ask", "outside_workspace": "ask"}},
            "agents": {"p": {"tool": "pi", "policy": "careful"}},
        }
    )
    profile = cfg.agents["p"]
    policy = config.effective_policy(cfg, profile)
    gaps = config.capability_gaps(cfg, profile, policy)
    assert "network" in gaps
    assert "destructive_command" in gaps
