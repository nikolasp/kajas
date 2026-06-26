"""Tests for the project registry and bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest

from kajas import projects
from kajas.config import GlobalConfig, write_global_config


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch):
    """Point Kajas at a fresh config dir for each test."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    monkeypatch.setenv("KAJAS_CONFIG_DIR", str(cfg_dir))
    write_global_config(GlobalConfig.model_validate({}), cfg_dir / "config.yaml")
    yield


def test_bootstrap_creates_kajas_dir(tmp_path: Path) -> None:
    target = tmp_path / "myrepo"
    target.mkdir()
    info = projects.bootstrap_project("myrepo", target, create_kajas_dir=True)
    assert info.has_kajas_dir
    assert (target / ".kajas" / "config.yaml").exists()
    assert any(p.name == "myrepo" for p in projects.list_projects())


def test_bootstrap_without_kajas_dir(tmp_path: Path) -> None:
    target = tmp_path / "myrepo"
    target.mkdir()
    info = projects.bootstrap_project("myrepo", target, create_kajas_dir=False)
    assert not info.has_kajas_dir
    assert not (target / ".kajas").exists()


def test_bootstrap_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        projects.bootstrap_project("nope", tmp_path / "missing")


def test_unregister_does_not_delete_files(tmp_path: Path) -> None:
    target = tmp_path / "myrepo"
    target.mkdir()
    projects.bootstrap_project("myrepo", target)
    assert (target / ".kajas").exists()
    assert projects.unregister_project("myrepo") is True
    assert (target / ".kajas").exists()  # files untouched
    assert projects.unregister_project("myrepo") is False


def test_bootstrap_twice_raises(tmp_path: Path) -> None:
    target = tmp_path / "myrepo"
    target.mkdir()
    projects.bootstrap_project("myrepo", target)
    with pytest.raises(ValueError):
        projects.bootstrap_project("myrepo", target)
