"""Tests for the persisted run store boundary."""

from __future__ import annotations

from pathlib import Path

from kajas.adapters.base import NormalizedEvent
from kajas.run_store import RunStore
from kajas.runs import RunRecord


def _record(project_path: Path, *, status: str = "draft") -> RunRecord:
    return RunRecord(
        id="2026-06-25-120000-store-test",
        project_path=str(project_path),
        project_name="proj",
        status=status,
        workflow="default",
        title="Store test",
        prompt="Do it",
        planner_agent="planner",
        implementor_agent="implementor",
        usage={"planning": None, "implementation": None, "verification": None},
        effective_config={},
    )


def test_run_store_creates_loads_and_replays_run(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    store = RunStore()
    record = _record(project_path)

    rdir = store.create_run(project_path, record, planner_prompt="# Planner\n")
    store.append_event(
        rdir,
        NormalizedEvent(
            type="status",
            stage="planning",
            status="planning",
            text="planning",
        ),
    )

    loaded = store.read_record(project_path, record.id)
    assert loaded is not None
    assert loaded.id == record.id
    assert store.discover(project_path)[0].id == record.id
    assert store.plan_texts(project_path, record.id) == (
        "# Plan\n\n_Pending planner output._\n",
        "# Approved Plan\n\n_Pending user approval._\n",
    )
    assert '"type":"status"' in store.replay_event_lines(project_path, record.id)[0]


def test_run_store_marks_in_flight_runs_interrupted(tmp_path: Path) -> None:
    project_path = tmp_path / "proj"
    project_path.mkdir()
    store = RunStore()
    record = _record(project_path, status="implementing")
    store.create_run(project_path, record, planner_prompt="# Planner\n")

    assert store.mark_interrupted_on_startup(project_path) == [record.id]
    loaded = store.read_record(project_path, record.id)
    assert loaded is not None
    assert loaded.status == "interrupted"
    assert loaded.error == "Server restarted while run was in flight."
