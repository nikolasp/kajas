"""Configuration schemas, loading, merging, and persistence.

Kajas uses two YAML files:

* global config at ``~/.config/kajas/config.yaml``
* project config at ``<repo>/.kajas/config.yaml``

Project config overrides global config by key (deep merge of mapping
nodes, replacement of scalars/lists). The merged view is what the
``GET /api/config/merged`` endpoint returns, and the run orchestrator
snapshots the merged view into each run folder so config changes never
mutate an active run.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import paths

# Policy values, taken verbatim from the v1 design.
PolicyValue = Literal["allow", "ask", "deny"]

# A policy is keyed by these fields. The keys are also the only fields an
# adapter can declare ``supports`` for.
POLICY_FIELDS = ("network", "destructive_command", "outside_workspace")

# Effective role is informative only in v1; it documents which agent is
# doing what. We do not enforce routing rules on it yet.
AgentRole = Literal["planner", "implementor", "reviewer", "general"]


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 8765
    trusted_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # First-run default is "no auth required" so the bootstrap screen can
    # prompt the user for a passphrase. ``bootstrap`` flips this on and
    # sets ``passphrase_hash`` and ``session_secret``.
    enabled: bool = False
    passphrase_hash: str | None = None
    session_secret: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "AuthConfig":
        if self.enabled and not self.passphrase_hash:
            raise ValueError("auth.enabled is true but passphrase_hash is not set")
        return self


class ProjectEntry(BaseModel):
    """One entry in the global ``projects:`` list."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: str

    @field_validator("path")
    @classmethod
    def _expand(cls, v: str) -> str:
        return os.path.expanduser(v)


class ToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    mode: Literal["text", "json", "jsonl", "rpc", "auto"] = "auto"
    env: dict[str, str] = Field(default_factory=dict)


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    model: str = "default"
    role: AgentRole = "general"
    policy: str | None = None
    allow_unenforced_policy: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network: PolicyValue = "ask"
    destructive_command: PolicyValue = "ask"
    outside_workspace: PolicyValue = "ask"
    allow_unenforced_policy: bool = False


class ApprovalGateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pause_before_implementation: bool = True
    pause_amendment: bool = False
    pause_final_acceptance: bool = False


class VerificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commands: list[str] = Field(default_factory=list)
    require_clean_worktree: bool = False
    require_final_summary: bool = True


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planner: str
    implementor: str
    approval_gate_set: str = "default"
    verification: VerificationSpec = Field(default_factory=VerificationSpec)


class AdapterCapabilities(BaseModel):
    """What policy fields an adapter can enforce."""

    model_config = ConfigDict(extra="forbid")

    sandbox: bool = False
    approval_policy: bool = False
    working_dir: bool = False
    network_gate: bool | Literal["partial"] = False
    destructive_gate: bool | Literal["partial"] = False


class AdapterSpec(BaseModel):
    """A registered tool adapter. Wraps ``ToolConfig`` with capabilities."""

    model_config = ConfigDict(extra="forbid")

    command: str
    mode: Literal["text", "json", "jsonl", "rpc", "auto"] = "auto"
    env: dict[str, str] = Field(default_factory=dict)
    supports: AdapterCapabilities = Field(default_factory=AdapterCapabilities)


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    projects: list[ProjectEntry] = Field(default_factory=list)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    adapters: dict[str, AdapterSpec] = Field(default_factory=dict)
    agents: dict[str, AgentProfile] = Field(default_factory=dict)
    policies: dict[str, PolicySpec] = Field(default_factory=dict)
    approval_gate_sets: dict[str, ApprovalGateSet] = Field(default_factory=dict)
    workflows: dict[str, WorkflowSpec] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    """The trimmed set of fields allowed under a project's ``.kajas/config.yaml``.

    Anything else (e.g. ``projects:`` list) is intentionally not allowed
    inside a project config; project config exists only to override parts
    of the global config.
    """

    model_config = ConfigDict(extra="forbid")

    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    adapters: dict[str, AdapterSpec] = Field(default_factory=dict)
    agents: dict[str, AgentProfile] = Field(default_factory=dict)
    policies: dict[str, PolicySpec] = Field(default_factory=dict)
    approval_gate_sets: dict[str, ApprovalGateSet] = Field(default_factory=dict)
    workflows: dict[str, WorkflowSpec] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading and merging
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping, got {type(data).__name__}")
    return data


def _deep_merge(base: Any, override: Any) -> Any:
    """Deep merge ``override`` onto ``base``.

    Mappings merge recursively, lists and scalars are replaced. ``None``
    values in ``override`` are skipped so a project config can use ``~``
    to mean "inherit from global".
    """
    if override is None:
        return base
    if isinstance(base, dict) and isinstance(override, dict):
        merged: dict[str, Any] = dict(base)
        for key, value in override.items():
            if value is None:
                continue
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return override


def load_global_config(path: Path | None = None) -> GlobalConfig:
    path = path or paths.global_config_path()
    return GlobalConfig.model_validate(_read_yaml(path))


def load_project_config(project_path: Path) -> ProjectConfig:
    config_path = project_path / ".kajas" / "config.yaml"
    return ProjectConfig.model_validate(_read_yaml(config_path))


def load_project_config_raw(project_path: Path) -> dict[str, Any]:
    """Read a project config without validating it.

    Project config files are allowed to contain partial overrides
    (e.g. only the ``model`` of an agent); :func:`merge_configs` performs
    the deep merge and only then validates the result.
    """
    config_path = project_path / ".kajas" / "config.yaml"
    return _read_yaml(config_path)


def merge_configs(
    global_cfg: GlobalConfig,
    project_cfg: ProjectConfig | dict[str, Any] | None,
) -> GlobalConfig:
    """Return a new ``GlobalConfig`` with project overrides applied.

    ``project_cfg`` may be a :class:`ProjectConfig` instance or a raw
    ``dict`` (so callers can pass a partial override without first
    validating it as a full ProjectConfig, which would reject partial
    agent profiles).
    """
    base: dict[str, Any] = global_cfg.model_dump()
    if project_cfg is None:
        overlay: dict[str, Any] = {}
    elif isinstance(project_cfg, ProjectConfig):
        overlay = project_cfg.model_dump()
    else:
        overlay = dict(project_cfg)
    merged = _deep_merge(base, overlay)
    return GlobalConfig.model_validate(merged)


def write_global_config(cfg: GlobalConfig, path: Path | None = None) -> None:
    path = path or paths.global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    dump = cfg.model_dump(mode="json")
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(dump, fh, sort_keys=False, allow_unicode=True)


def write_project_config(cfg: ProjectConfig, project_path: Path) -> None:
    kajas_dir = project_path / ".kajas"
    kajas_dir.mkdir(parents=True, exist_ok=True)
    path = kajas_dir / "config.yaml"
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            cfg.model_dump(mode="json"), fh, sort_keys=False, allow_unicode=True
        )


def iter_projects(cfg: GlobalConfig) -> Iterable[ProjectEntry]:
    return iter(cfg.projects)


def find_project(cfg: GlobalConfig, name: str) -> ProjectEntry | None:
    for entry in cfg.projects:
        if entry.name == name:
            return entry
    return None


def remove_project(cfg: GlobalConfig, name: str) -> bool:
    for i, entry in enumerate(cfg.projects):
        if entry.name == name:
            del cfg.projects[i]
            return True
    return False


def add_project(cfg: GlobalConfig, name: str, path: str) -> None:
    if find_project(cfg, name) is not None:
        raise ValueError(f"project {name!r} is already registered")
    cfg.projects.append(ProjectEntry(name=name, path=path))


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when the merged config references things that don't exist."""


def validate_for_runtime(cfg: GlobalConfig) -> list[str]:
    """Check that agents, workflows, policies, and adapters cross-reference cleanly."""
    errors: list[str] = []
    for profile_name, profile in cfg.agents.items():
        if profile.tool not in cfg.tools and profile.tool not in cfg.adapters:
            errors.append(
                f"agent {profile_name!r} references unknown tool {profile.tool!r}"
            )
        if profile.policy is not None and profile.policy not in cfg.policies:
            errors.append(
                f"agent {profile_name!r} references unknown policy {profile.policy!r}"
            )
    for wf_name, wf in cfg.workflows.items():
        for role, agent_name in (("planner", wf.planner), ("implementor", wf.implementor)):
            if agent_name not in cfg.agents:
                errors.append(
                    f"workflow {wf_name!r} {role} references unknown agent {agent_name!r}"
                )
        if wf.approval_gate_set not in cfg.approval_gate_sets:
            errors.append(
                f"workflow {wf_name!r} references unknown approval_gate_set "
                f"{wf.approval_gate_set!r}"
            )
    return errors


def effective_policy(cfg: GlobalConfig, profile: AgentProfile) -> PolicySpec:
    """Resolve a profile's effective policy (its own named policy, or the default ``careful``)."""
    if profile.policy is not None:
        return cfg.policies[profile.policy]
    return cfg.policies.get("careful", PolicySpec())


def capability_gaps(
    cfg: GlobalConfig, profile: AgentProfile, policy: PolicySpec
) -> list[str]:
    """Return the list of policy fields the adapter cannot enforce.

    Empty list means the adapter can fully enforce the requested policy.
    """
    adapter = cfg.adapters.get(profile.tool) or _adapter_from_tool(cfg, profile.tool)
    if adapter is None:
        # No adapter registered; treat as "supports nothing".
        return list(POLICY_FIELDS)

    caps = adapter.supports
    gaps: list[str] = []

    def _check(field: str, gate_attr: str) -> None:
        gate = getattr(caps, gate_attr)
        if gate is True:
            return
        if gate == "partial" and getattr(policy, field) != "ask":
            gaps.append(field)
        elif gate is False:
            gaps.append(field)

    _check("network", "network_gate")
    _check("destructive_command", "destructive_gate")
    if not caps.working_dir:
        gaps.append("outside_workspace")
    return gaps


def _adapter_from_tool(cfg: GlobalConfig, tool_name: str) -> AdapterSpec | None:
    """Backwards-compat: treat a ``tools:`` entry as a bare adapter with no capabilities."""
    tool = cfg.tools.get(tool_name)
    if tool is None:
        return None
    return AdapterSpec(command=tool.command, mode=tool.mode, env=dict(tool.env))


__all__ = [
    "AgentProfile",
    "AgentRole",
    "ApprovalGateSet",
    "AuthConfig",
    "AdapterCapabilities",
    "AdapterSpec",
    "ConfigError",
    "GlobalConfig",
    "POLICY_FIELDS",
    "PolicySpec",
    "PolicyValue",
    "ProjectConfig",
    "ProjectEntry",
    "ServerConfig",
    "ToolConfig",
    "VerificationSpec",
    "WorkflowSpec",
    "add_project",
    "capability_gaps",
    "copy",
    "effective_policy",
    "find_project",
    "iter_projects",
    "load_global_config",
    "load_project_config",
    "merge_configs",
    "remove_project",
    "validate_for_runtime",
    "write_global_config",
    "write_project_config",
]
