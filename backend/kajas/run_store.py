"""Persistent run storage.

``RunStore`` is the boundary around ``<project>/.kajas/runs``. It owns the
on-disk file layout, run markdown, event history, replay, restart sweeps, and
deletion. The orchestrator still owns live process state.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

import yaml

from .adapters.base import NormalizedEvent
from .runs import IN_FLIGHT_STATUSES, RunRecord


class RunStore:
    """Access persisted run folders for one or more projects."""

    events_file = "events.ndjson"
    run_file = "run.md"
    plan_file = "plan.md"
    approved_plan_file = "plan.approved.md"
    approvals_file = "approvals.md"

    def runs_dir(self, project_path: Path) -> Path:
        return project_path / ".kajas" / "runs"

    def run_dir(self, project_path: Path, run_id: str) -> Path:
        return self.runs_dir(project_path) / run_id

    def exists(self, project_path: Path, run_id: str) -> bool:
        return self.run_dir(project_path, run_id).exists()

    def create_run(
        self,
        project_path: Path,
        record: RunRecord,
        *,
        planner_prompt: str,
    ) -> Path:
        rdir = self.run_dir(project_path, record.id)
        (rdir / "prompts").mkdir(parents=True, exist_ok=True)
        (rdir / "raw").mkdir(parents=True, exist_ok=True)
        self.write_text(rdir, "prompts/planner.md", planner_prompt)
        self.write_text(rdir, self.plan_file, "# Plan\n\n_Pending planner output._\n")
        self.write_text(
            rdir,
            self.approved_plan_file,
            "# Approved Plan\n\n_Pending user approval._\n",
        )
        self.write_text(rdir, self.approvals_file, "# Approvals\n")
        self.write_events(rdir, [])
        self.save_record(rdir, record)
        return rdir

    def save_record(self, rdir: Path, record: RunRecord) -> None:
        self.write_run_md(record, self.render_run_body(record), rdir / self.run_file)

    def write_run_md(self, record: RunRecord, body: str, path: Path) -> None:
        front = record.model_dump(mode="json")
        payload = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        path.write_text(f"---\n{payload}---\n\n{body}\n", encoding="utf-8")

    def read_run_md(self, path: Path) -> tuple[RunRecord, str]:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"{path} is not a run.md file (missing frontmatter)")
        end = text.find("\n---\n", 4)
        if end == -1:
            raise ValueError(f"{path} is not a run.md file (unterminated frontmatter)")
        fm = yaml.safe_load(text[4:end])
        body = text[end + 5 :].lstrip("\n")
        record = RunRecord.model_validate(fm)
        return record, body

    def render_run_body(self, record: RunRecord) -> str:
        parts: list[str] = []
        parts.append(f"# {record.title or record.id}\n")
        parts.append("## Prompt\n")
        parts.append(record.prompt.strip() + "\n")
        parts.append("## Effective Config Snapshot\n")
        parts.append(
            "```yaml\n"
            + yaml.safe_dump(
                record.effective_config, sort_keys=False, allow_unicode=True
            )
            + "```\n"
        )
        if record.error:
            parts.append("## Error\n")
            parts.append(record.error + "\n")
        if record.final_summary:
            parts.append("## Final Summary\n")
            parts.append(record.final_summary + "\n")
        return "\n".join(parts)

    def read_record(self, project_path: Path, run_id: str) -> RunRecord | None:
        loaded = self.read_record_with_body(project_path, run_id)
        return loaded[0] if loaded is not None else None

    def read_record_with_body(
        self, project_path: Path, run_id: str
    ) -> tuple[RunRecord, str] | None:
        run_md = self.run_dir(project_path, run_id) / self.run_file
        if not run_md.exists():
            return None
        return self.read_run_md(run_md)

    def discover(self, project_path: Path) -> list[RunRecord]:
        runs_dir = self.runs_dir(project_path)
        if not runs_dir.exists():
            return []
        records: list[RunRecord] = []
        for child in sorted(runs_dir.iterdir()):
            run_md = child / self.run_file
            if not run_md.is_file():
                continue
            try:
                record, _ = self.read_run_md(run_md)
            except Exception:  # noqa: BLE001
                continue
            records.append(record)
        return records

    def append_event(self, rdir: Path, ev: NormalizedEvent) -> None:
        with (rdir / self.events_file).open("a", encoding="utf-8") as fh:
            fh.write(ev.model_dump_json() + "\n")

    def write_events(self, rdir: Path, lines: Iterable[str]) -> None:
        text = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
        (rdir / self.events_file).write_text(text, encoding="utf-8")

    def replay_event_lines(self, project_path: Path, run_id: str) -> list[str]:
        events_path = self.run_dir(project_path, run_id) / self.events_file
        if not events_path.exists():
            return []
        return [
            line
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def read_text(self, rdir: Path, relative_path: str) -> str | None:
        path = rdir / relative_path
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_text(self, rdir: Path, relative_path: str, text: str) -> None:
        path = rdir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def plan_texts(
        self, project_path: Path, run_id: str
    ) -> tuple[str | None, str | None]:
        rdir = self.run_dir(project_path, run_id)
        return (
            self.read_text(rdir, self.plan_file),
            self.read_text(rdir, self.approved_plan_file),
        )

    def mark_interrupted_on_startup(self, project_path: Path) -> list[str]:
        interrupted: list[str] = []
        for record in self.discover(project_path):
            if record.status in IN_FLIGHT_STATUSES:
                record.status = "interrupted"
                record.error = "Server restarted while run was in flight."
                record.touch()
                self.save_record(self.run_dir(project_path, record.id), record)
                interrupted.append(record.id)
        return interrupted

    def delete(self, project_path: Path, run_id: str) -> None:
        shutil.rmtree(self.run_dir(project_path, run_id), ignore_errors=True)


DEFAULT_RUN_STORE = RunStore()
