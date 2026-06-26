"""Tests for the FastAPI server, using FastAPI's TestClient.

The tests use the fake workflow so we don't need real Codex or Pi
invocations. Each test resets the global config and the active
orchestrator so they don't leak state between tests.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def kajas_env(monkeypatch, tmp_path: Path):
    """Point Kajas at a fresh config + data dir per test."""
    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    cfg_dir.mkdir()
    data_dir.mkdir()
    monkeypatch.setenv("KAJAS_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("KAJAS_DATA_DIR", str(data_dir))
    # Import inside the fixture so the env vars are visible.
    from kajas import config, paths
    from kajas.config import GlobalConfig, AuthConfig

    paths.global_config_path  # touch
    cfg = GlobalConfig.model_validate(
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
                "planner": {"tool": "fake", "policy": "careful", "role": "planner"},
                "implementor": {"tool": "fake", "policy": "careful", "role": "implementor"},
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
            "auth": {"enabled": False},
        }
    )
    config.write_global_config(cfg, paths.global_config_path())
    yield cfg_dir, data_dir


@pytest.fixture()
def client(kajas_env):
    from kajas.server import create_app

    app = create_app()
    # Replace the orchestrator with a fresh instance per test.
    app.state.orchestrator = __import__("kajas.runs", fromlist=["Orchestrator"]).Orchestrator()
    return TestClient(app)


def test_health_endpoint(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "checks" in body
    assert isinstance(body["checks"], list)


def test_bootstrap_then_login_then_logout(client):
    # Auth is disabled in the fixture config, so we first need to bootstrap it.
    r = client.post("/api/auth/bootstrap", json={"passphrase": "hunter2"})
    assert r.status_code == 200
    # Now login
    r = client.post("/api/auth/login", json={"passphrase": "hunter2"})
    assert r.status_code == 200, r.text
    assert r.cookies.get("kajas_session")
    # Logout
    r = client.post("/api/auth/logout")
    assert r.status_code == 200


def test_bootstrap_from_empty_config_seeds_default_agents():
    from kajas.config import GlobalConfig
    from kajas.server import _seed_runtime_defaults_if_empty

    cfg = _seed_runtime_defaults_if_empty(GlobalConfig.model_validate({}))
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


def test_login_wrong_passphrase_is_401(client):
    client.post("/api/auth/bootstrap", json={"passphrase": "hunter2"})
    r = client.post("/api/auth/login", json={"passphrase": "wrong"})
    assert r.status_code == 401


def test_create_and_inspect_project(client, tmp_path):
    target = tmp_path / "myrepo"
    target.mkdir()
    r = client.post(
        "/api/projects",
        json={"name": "myrepo", "path": str(target), "create_kajas_dir": True},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "myrepo"
    assert body["has_kajas_dir"] is True
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert any(p["name"] == "myrepo" for p in r.json())


def test_merged_config(client, kajas_env, tmp_path):
    target = tmp_path / "myrepo"
    target.mkdir()
    client.post("/api/projects", json={"name": "myrepo", "path": str(target)})
    r = client.get("/api/config/merged", params={"project": "myrepo"})
    assert r.status_code == 200
    cfg = r.json()
    assert "fake" in cfg["adapters"]
    assert "careful" in cfg["policies"]


def test_create_run_and_approve(client, kajas_env, tmp_path):
    target = tmp_path / "myrepo"
    target.mkdir()
    client.post("/api/projects", json={"name": "myrepo", "path": str(target)})
    r = client.post(
        "/api/runs",
        json={
            "project": "myrepo",
            "workflow": "default",
            "title": "test",
            "prompt": "<!-- kajas:fake mode=happy -->\ndo it",
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()
    run_id = run["id"]
    # Wait for the run to reach awaiting_plan_approval
    import time

    for _ in range(50):
        r = client.get(f"/api/runs/{run_id}")
        if r.json()["status"] == "awaiting_plan_approval":
            break
        time.sleep(0.1)
    else:
        pytest.fail("run did not reach awaiting_plan_approval")
    r = client.post(f"/api/runs/{run_id}/approve-plan", json={})
    assert r.status_code == 200
    for _ in range(50):
        r = client.get(f"/api/runs/{run_id}")
        if r.json()["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert r.json()["status"] == "completed"


def test_cancel_run_awaiting_plan_approval_returns_cancelled(client, kajas_env, tmp_path):
    target = tmp_path / "myrepo"
    target.mkdir()
    client.post("/api/projects", json={"name": "myrepo", "path": str(target)})
    r = client.post(
        "/api/runs",
        json={
            "project": "myrepo",
            "workflow": "default",
            "title": "cancel test",
            "prompt": "<!-- kajas:fake mode=happy -->\ndo it",
        },
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]

    import time

    for _ in range(50):
        r = client.get(f"/api/runs/{run_id}")
        if r.json()["status"] == "awaiting_plan_approval":
            break
        time.sleep(0.1)
    else:
        pytest.fail("run did not reach awaiting_plan_approval")

    r = client.post(f"/api/runs/{run_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"

    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_create_and_list_benchmark(client, monkeypatch):
    from kajas import benchmarks, server

    def complete_immediately(run_id, payload):
        run = benchmarks.DEFAULT_BENCHMARK_STORE.read(run_id)
        assert run is not None
        run.status = "completed"
        run.model = payload.model or "local-model"
        run.scores = {
            "tool_calling": 25,
            "context_retrieval": 25,
            "coding": 35,
            "latency_reliability": 15,
        }
        run.total_score = 100
        run.usable = True
        benchmarks.DEFAULT_BENCHMARK_STORE.save(run)

    monkeypatch.setattr(server, "start_benchmark_task", complete_immediately)

    r = client.post(
        "/api/benchmarks",
        json={
            "base_url": "http://localhost:11434/v1",
            "model": "llama-local",
            "custom_headers": {"X-Test": "1"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "running"

    r = client.get("/api/benchmarks")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["model"] == "llama-local"
    assert runs[0]["total_score"] == 100

    r = client.get(f"/api/benchmarks/{body['id']}")
    assert r.status_code == 200
    assert r.json()["usable"] is True

    r = client.delete(f"/api/benchmarks/{body['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/api/benchmarks")
    assert r.status_code == 200
    assert r.json() == []

    r = client.get(f"/api/benchmarks/{body['id']}")
    assert r.status_code == 404
