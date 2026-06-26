"""Health checks for Kajas.

The basic checks are non-destructive and safe to run on demand from the
Web UI. Tool smoke checks are opt-in (and gated by a token-usage
warning) because they may invoke real CLI tools.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.base import HealthResult, load_registry
from .config import (
    GlobalConfig,
    capability_gaps,
    effective_policy,
    load_global_config,
    load_project_config,
    validate_for_runtime,
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _project_check(project) -> CheckResult:
    project_path = Path(project.path)
    if not project_path.exists():
        return CheckResult(
            name=f"project:{project.name}",
            ok=False,
            detail=f"path does not exist: {project.path}",
        )
    kajas_dir = project_path / ".kajas"
    if not kajas_dir.exists():
        return CheckResult(
            name=f"project:{project.name}",
            ok=True,
            detail="registered, no .kajas/ yet",
        )
    config_path = kajas_dir / "config.yaml"
    if config_path.exists():
        try:
            load_project_config(project_path)
            return CheckResult(
                name=f"project:{project.name}", ok=True, detail="config parses"
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(
                name=f"project:{project.name}", ok=False, detail=f"config error: {exc}"
            )
    return CheckResult(
        name=f"project:{project.name}", ok=True, detail="no config.yaml"
    )


def _write_access_check(project) -> CheckResult:
    project_path = Path(project.path)
    if not project_path.exists():
        return CheckResult(
            name=f"project:{project.name}:write",
            ok=False,
            detail="path missing",
        )
    probe = project_path / ".kajas" / ".kajas-write-probe"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return CheckResult(
            name=f"project:{project.name}:write", ok=False, detail=str(exc)
        )
    return CheckResult(
        name=f"project:{project.name}:write", ok=True, detail="writable"
    )


def _tool_on_path(tool_name: str) -> tuple[bool, str]:
    if shutil.which(tool_name) is None:
        return False, f"{tool_name} not on PATH"
    return True, shutil.which(tool_name) or ""


def run_basic_checks(cfg: GlobalConfig | None = None) -> list[CheckResult]:
    cfg = cfg or load_global_config()
    results: list[CheckResult] = []

    # Global config
    try:
        load_global_config()
        results.append(CheckResult(name="config:global", ok=True, detail="parses"))
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(name="config:global", ok=False, detail=str(exc)))

    # Auth
    if cfg.auth.enabled:
        if cfg.auth.passphrase_hash and cfg.auth.session_secret:
            results.append(CheckResult(name="auth", ok=True, detail="configured"))
        else:
            results.append(
                CheckResult(
                    name="auth",
                    ok=False,
                    detail="enabled but passphrase_hash/session_secret missing",
                )
            )
    else:
        results.append(CheckResult(name="auth", ok=True, detail="disabled"))

    # Cross-reference validation
    errors = validate_for_runtime(cfg)
    if errors:
        results.append(CheckResult(name="config:references", ok=False, detail="; ".join(errors)))
    else:
        results.append(CheckResult(name="config:references", ok=True, detail="ok"))

    # Per-project checks
    for project in cfg.projects:
        results.append(_project_check(project))
        results.append(_write_access_check(project))

    # Adapter capability gaps
    for wf_name, wf in cfg.workflows.items():
        for role, agent_name in (("planner", wf.planner), ("implementor", wf.implementor)):
            agent = cfg.agents.get(agent_name)
            if agent is None:
                continue
            policy = effective_policy(cfg, agent)
            gaps = capability_gaps(cfg, agent, policy)
            if gaps:
                results.append(
                    CheckResult(
                        name=f"capability:{wf_name}:{role}",
                        ok=policy.allow_unenforced_policy or agent.allow_unenforced_policy,
                        detail=(
                            f"tool {agent.tool!r} cannot enforce: {', '.join(gaps)}"
                            + (" (allowed via allow_unenforced_policy)" if policy.allow_unenforced_policy else "")
                        ),
                    )
                )
            else:
                results.append(
                    CheckResult(
                        name=f"capability:{wf_name}:{role}",
                        ok=True,
                        detail=f"{agent.tool} can enforce policy",
                    )
                )

    return results


def run_tool_smoke(cfg: GlobalConfig | None = None) -> list[CheckResult]:
    """Run a (possibly token-consuming) tool check. Caller should warn the user."""
    cfg = cfg or load_global_config()
    results: list[CheckResult] = []

    # Ensure each tool is on PATH before instantiating the adapter.
    tool_names = sorted({agent.tool for agent in cfg.agents.values()})
    adapters = load_registry(tool_names)
    for tool in tool_names:
        if tool not in adapters:
            ok, detail = _tool_on_path(tool)
            results.append(
                CheckResult(
                    name=f"smoke:tool:{tool}",
                    ok=False,
                    detail=f"adapter for {tool!r} is not registered" if ok else detail,
                )
            )
            continue
        health = adapters[tool].doctor(smoke=True)
        results.append(
            CheckResult(
                name=f"smoke:tool:{tool}",
                ok=health.ok,
                detail=health.detail,
            )
        )
    return results


def summarize(results: list[CheckResult]) -> dict[str, Any]:
    return {
        "ok": all(r.ok for r in results),
        "checks": [
            {"name": r.name, "ok": r.ok, "detail": r.detail, "extra": r.extra}
            for r in results
        ],
    }


__all__ = [
    "CheckResult",
    "HealthResult",
    "run_basic_checks",
    "run_tool_smoke",
    "summarize",
]
