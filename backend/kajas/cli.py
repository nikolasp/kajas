"""``kajas`` command line entrypoint.

Usage::

    kajas serve [--host HOST] [--port PORT] [--no-browser]
    kajas init-project <name> <path> [--no-bootstrap-dir]
    kajas run --project NAME --workflow NAME --prompt "..."
    kajas doctor [--tool-smoke | --no-tool-smoke]
    kajas init  # first-run helper: writes starter global config

All commands are thin wrappers over the same backend services the Web
UI uses. ``init`` and ``serve --no-bootstrap`` are the only entrypoints
that don't require auth.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import webbrowser
from pathlib import Path

from . import paths
from .adapters.base import load_registry
from .auth import generate_session_secret, hash_passphrase
from .config import (
    AdapterSpec,
    AgentProfile,
    ApprovalGateSet,
    AuthConfig,
    GlobalConfig,
    PolicySpec,
    ServerConfig,
    ToolConfig,
    VerificationSpec,
    WorkflowSpec,
    load_global_config,
    write_global_config,
)
from .doctor import run_basic_checks, run_tool_smoke
from .projects import bootstrap_project, list_projects


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


DEFAULT_GLOBAL_CONFIG: dict = {
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "trusted_hosts": ["localhost", "127.0.0.1"],
    },
    "auth": {
        # Disabled by default; ``kajas init`` flips this on.
        "enabled": False,
    },
    "projects": [],
    "tools": {
        "codex": {
            "command": "codex",
            "mode": "json",
            "env": {"OPENAI_API_KEY": "env:OPENAI_API_KEY"},
        },
        "pi": {
            "command": "pi",
            "mode": "json",
            "env": {"PI_API_KEY": "env:PI_API_KEY"},
        },
        "fake": {
            "command": "fake",
            "mode": "json",
            "env": {},
        },
    },
    "adapters": {
        "codex": {
            "command": "codex",
            "mode": "json",
            "env": {"OPENAI_API_KEY": "env:OPENAI_API_KEY"},
            "supports": {
                "sandbox": True,
                "approval_policy": True,
                "working_dir": True,
                "network_gate": "partial",
                "destructive_gate": "partial",
            },
        },
        "pi": {
            "command": "pi",
            "mode": "json",
            "env": {"PI_API_KEY": "env:PI_API_KEY"},
            "supports": {
                "sandbox": False,
                "approval_policy": False,
                "working_dir": True,
                "network_gate": False,
                "destructive_gate": False,
            },
        },
        "fake": {
            "command": "fake",
            "mode": "json",
            "env": {},
            "supports": {
                "sandbox": True,
                "approval_policy": True,
                "working_dir": True,
                "network_gate": True,
                "destructive_gate": True,
            },
        },
    },
    "agents": {
        "planner": {
            "tool": "codex",
            "model": "gpt-5.5",
            "role": "planner",
            "policy": "careful",
        },
        "coder": {
            "tool": "pi",
            "model": "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf",
            "role": "implementor",
            "policy": "careful",
            "allow_unenforced_policy": True,
            "extra": {
                "local_model": "Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf",
            },
        },
        "fake_planner": {
            "tool": "fake",
            "model": "default",
            "role": "planner",
            "policy": "careful",
        },
        "fake_implementor": {
            "tool": "fake",
            "model": "default",
            "role": "implementor",
            "policy": "careful",
        },
    },
    "policies": {
        "careful": {
            "network": "ask",
            "destructive_command": "ask",
            "outside_workspace": "ask",
            "allow_unenforced_policy": False,
        },
    },
    "approval_gate_sets": {
        "default": {
            "pause_before_implementation": True,
            "pause_amendment": False,
            "pause_final_acceptance": False,
        },
    },
    "workflows": {
        "default": {
            "planner": "planner",
            "implementor": "coder",
            "approval_gate_set": "default",
            "verification": {
                "commands": [],
                "require_clean_worktree": False,
                "require_final_summary": True,
            },
        },
        "fake": {
            "planner": "fake_planner",
            "implementor": "fake_implementor",
            "approval_gate_set": "default",
            "verification": {
                "commands": [],
                "require_clean_worktree": False,
                "require_final_summary": True,
            },
        },
    },
}


def default_global_config() -> GlobalConfig:
    return GlobalConfig.model_validate(DEFAULT_GLOBAL_CONFIG)


def write_default_global_config(overwrite: bool = False) -> Path:
    path = paths.global_config_path()
    if path.exists() and not overwrite:
        return path
    cfg = default_global_config()
    write_global_config(cfg)
    return path


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    cfg_path = write_default_global_config(overwrite=args.force)
    print(f"wrote global config to {cfg_path}")
    if not args.skip_passphrase:
        import getpass

        pw = getpass.getpass("set admin passphrase: ")
        confirm = getpass.getpass("confirm: ")
        if pw != confirm or not pw:
            print("passphrases do not match (or empty)", file=sys.stderr)
            return 2
        cfg = load_global_config()
        cfg.auth = AuthConfig(
            enabled=True,
            passphrase_hash=hash_passphrase(pw),
            session_secret=generate_session_secret(),
        )
        write_global_config(cfg)
        print("auth enabled; restart `kajas serve` to log in")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = load_global_config()
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    if host == "0.0.0.0":
        print(
            "WARNING: binding 0.0.0.0 exposes the Kajas API on every network "
            "interface. Use --i-understand-the-risks if you really mean it.",
            file=sys.stderr,
        )
        if not args.i_understand_the_risks:
            return 2

    from .server import create_app, mount_frontend

    app = create_app()
    if args.frontend_dir:
        mount_frontend(app, Path(args.frontend_dir).expanduser().resolve())
    if cfg.auth.enabled:
        print(f"Kajas serving on http://{host}:{port}  (auth: enabled)")
    else:
        print(f"Kajas serving on http://{host}:{port}  (auth: disabled)")
        print("Run `kajas init` to set a passphrase before exposing this server.")
    if args.open_browser and host in ("127.0.0.1", "localhost"):
        try:
            webbrowser.open(f"http://{host}:{port}/")
        except Exception:
            pass
    uvicorn.run(app, host=host, port=port, log_level=args.log_level)
    return 0


def cmd_init_project(args: argparse.Namespace) -> int:
    try:
        info = bootstrap_project(
            args.name, Path(args.path).expanduser(), create_kajas_dir=not args.no_bootstrap_dir
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"registered project {info.name} at {info.path}")
    if not info.is_git:
        print("warning: project is not a git repository", file=sys.stderr)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Headless version of the Web UI's New Run flow.

    Useful for smoke tests and for users who like a terminal. It does
    not (yet) support interactive plan editing; the plan is auto-approved.
    """
    from .config import load_project_config_raw, merge_configs
    from .runs import Orchestrator, delete_run_dir

    cfg = load_global_config()
    project = next((p for p in cfg.projects if p.name == args.project), None)
    if project is None:
        print(f"project {args.project!r} is not registered", file=sys.stderr)
        return 2
    project_path = Path(project.path)
    project_cfg = (
        load_project_config_raw(project_path) if (project_path / ".kajas").exists() else None
    )
    merged = merge_configs(cfg, project_cfg)
    if args.workflow not in merged.workflows:
        print(f"workflow {args.workflow!r} is not defined", file=sys.stderr)
        return 2

    orch = Orchestrator()
    handle = orch.create_run(
        project_name=project.name,
        project_path=project_path,
        workflow_name=args.workflow,
        title=args.title or args.prompt[:40],
        prompt=args.prompt,
    )
    orch.start(handle)
    # Headless mode auto-approves the plan after planning is done.
    while True:
        time.sleep(0.5)
        if handle.record.status == "awaiting_plan_approval":
            orch.approve_plan(handle, edited_plan=None)
        if handle.record.status in TERMINAL_HEADLESS:
            break
    summary = handle.record.model_dump(mode="json")
    print(json.dumps(summary, indent=2, default=str))
    if args.delete:
        delete_run_dir(project_path, handle.record.id)
    return 0


TERMINAL_HEADLESS = (
    "completed",
    "failed",
    "cancelled",
)


def cmd_doctor(args: argparse.Namespace) -> int:
    results = run_basic_checks()
    if args.tool_smoke:
        results.extend(run_tool_smoke())
    failures = [r for r in results if not r.ok]
    for r in results:
        marker = "ok" if r.ok else "FAIL"
        print(f"[{marker}] {r.name}: {r.detail}")
    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kajas", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="write starter global config and set passphrase")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_init.add_argument("--skip-passphrase", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_serve = sub.add_parser("serve", help="start the Web UI + API")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    p_serve.add_argument(
        "--open-browser", action="store_true", help="open the UI after starting"
    )
    p_serve.add_argument(
        "--frontend-dir",
        help="serve a built Vite dist from this directory (production mode)",
    )
    p_serve.add_argument("--log-level", default="info")
    p_serve.add_argument(
        "--i-understand-the-risks",
        action="store_true",
        help="acknowledge that binding 0.0.0.0 exposes the API",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_ip = sub.add_parser("init-project", help="register a project and bootstrap .kajas/")
    p_ip.add_argument("name")
    p_ip.add_argument("path")
    p_ip.add_argument("--no-bootstrap-dir", action="store_true")
    p_ip.set_defaults(func=cmd_init_project)

    p_run = sub.add_parser("run", help="run a workflow from the terminal (headless)")
    p_run.add_argument("--project", required=True)
    p_run.add_argument("--workflow", default="default")
    p_run.add_argument("--title", default="")
    p_run.add_argument("--prompt", required=True)
    p_run.add_argument("--delete", action="store_true", help="delete the run folder on exit")
    p_run.set_defaults(func=cmd_run)

    p_doc = sub.add_parser("doctor", help="run health checks")
    smoke = p_doc.add_mutually_exclusive_group()
    smoke.add_argument("--tool-smoke", dest="tool_smoke", action="store_true")
    smoke.add_argument("--no-tool-smoke", dest="tool_smoke", action="store_false")
    p_doc.set_defaults(tool_smoke=False, func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
