"""Project registration and bootstrap.

A project is just a directory Kajas has been told about. We do not walk
the filesystem; the user (or the Web UI) registers projects explicitly.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import (
    GlobalConfig,
    ProjectConfig,
    add_project,
    find_project,
    load_global_config,
    load_project_config,
    remove_project,
    write_global_config,
    write_project_config,
)


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    path: Path
    has_kajas_dir: bool
    is_git: bool
    config: ProjectConfig


def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def bootstrap_project(
    name: str, project_path: Path, *, create_kajas_dir: bool = True
) -> ProjectInfo:
    """Create a project entry, optionally initialising ``.kajas/``.

    Raises:
        FileNotFoundError: if ``project_path`` does not exist.
        ValueError: if the project is already registered.
    """
    project_path = project_path.resolve()
    if not project_path.exists():
        raise FileNotFoundError(f"project path does not exist: {project_path}")

    cfg = load_global_config()
    if find_project(cfg, name) is not None:
        raise ValueError(f"project {name!r} is already registered")

    if create_kajas_dir:
        kajas_dir = project_path / ".kajas"
        kajas_dir.mkdir(parents=True, exist_ok=True)
        config_path = kajas_dir / "config.yaml"
        if not config_path.exists():
            config_path.write_text(
                "# Project-local Kajas config. Anything you set here overrides the\n"
                "# matching key in the global config (~/.config/kajas/config.yaml).\n"
                "# Use `~` to inherit a key from the global config.\n",
                encoding="utf-8",
            )

    add_project(cfg, name=name, path=str(project_path))
    write_global_config(cfg)
    return inspect_project(name)


def inspect_project(name: str) -> ProjectInfo:
    cfg = load_global_config()
    entry = find_project(cfg, name)
    if entry is None:
        raise KeyError(f"project {name!r} is not registered")
    path = Path(entry.path)
    if not path.exists():
        return ProjectInfo(
            name=name,
            path=path,
            has_kajas_dir=(path / ".kajas").exists(),
            is_git=is_git_repo(path),
            config=ProjectConfig(),
        )
    return ProjectInfo(
        name=name,
        path=path,
        has_kajas_dir=(path / ".kajas").exists(),
        is_git=is_git_repo(path),
        config=load_project_config(path),
    )


def list_projects() -> list[ProjectInfo]:
    cfg = load_global_config()
    infos: list[ProjectInfo] = []
    for entry in cfg.projects:
        try:
            infos.append(inspect_project(entry.name))
        except KeyError:
            continue
    return infos


def unregister_project(name: str) -> bool:
    """Remove ``name`` from the registry. Does not touch the project files."""
    cfg = load_global_config()
    removed = remove_project(cfg, name)
    if removed:
        write_global_config(cfg)
    return removed


def delete_project_files(name: str) -> bool:
    """Remove ``<project>/.kajas`` and any runs inside it. Does not touch the registry."""
    try:
        info = inspect_project(name)
    except KeyError:
        return False
    kajas_dir = info.path / ".kajas"
    if kajas_dir.exists():
        shutil.rmtree(kajas_dir)
    return True


def git_status(path: Path) -> tuple[bool, str]:
    """Return (clean, summary). ``summary`` is human-readable, may be empty."""
    if not is_git_repo(path):
        return True, ""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"git status failed: {exc}"
    if proc.stdout.strip():
        return False, f"{len(proc.stdout.splitlines())} files changed"
    return True, "clean"


__all__ = [
    "ProjectInfo",
    "bootstrap_project",
    "delete_project_files",
    "git_status",
    "inspect_project",
    "is_git_repo",
    "list_projects",
    "unregister_project",
]
