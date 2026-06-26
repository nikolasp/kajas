"""FastAPI app exposing the Kajas HTTP/SSE API.

The API surface mirrors the v1 design's API sketch; routes are grouped
into auth, projects, config, runs, and health. The app is also
responsible for the first-run bootstrap handshake that lets the user
set a passphrase.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .auth import (
    SESSION_COOKIE,
    SessionUser,
    cookie_secure,
    generate_session_secret,
    hash_passphrase,
    issue_session,
    require_user,
    verify_passphrase,
)
from .config import (
    AuthConfig,
    GlobalConfig,
    ProjectConfig,
    load_global_config,
    load_project_config_raw,
    merge_configs,
    write_global_config,
    write_project_config,
)
from .doctor import run_basic_checks, run_tool_smoke, summarize
from .projects import (
    ProjectInfo,
    bootstrap_project,
    inspect_project,
    list_projects,
    unregister_project,
)
from .run_store import DEFAULT_RUN_STORE, RunStore
from .runs import (
    Orchestrator,
    RunRecord,
    TERMINAL_STATUSES,
)


log = logging.getLogger("kajas.server")

# A single orchestrator per process. Tests can create their own and
# inject via ``app.state.orchestrator``.
ORCHESTRATOR = Orchestrator()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    cfg = load_global_config()
    seeded = _seed_runtime_defaults_if_empty(cfg)
    if seeded is not cfg:
        write_global_config(seeded)
        cfg = seeded
    # On startup, sweep all known projects and mark in-flight runs as
    # ``interrupted`` so the UI can offer rerun / archive / delete.
    for project in cfg.projects:
        try:
            DEFAULT_RUN_STORE.mark_interrupted_on_startup(Path(project.path))
        except Exception:  # noqa: BLE001
            log.exception("interrupted sweep failed for %s", project.path)
    if not hasattr(app.state, "orchestrator"):
        app.state.orchestrator = ORCHESTRATOR
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kajas",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _wire_routes(app)
    return app


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passphrase: str


class BootstrapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passphrase: str = Field(min_length=4)


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    path: str
    create_kajas_dir: bool = True


class ProjectOut(BaseModel):
    name: str
    path: str
    has_kajas_dir: bool
    is_git: bool
    config: dict[str, Any]


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str
    workflow: str = "default"
    title: str = ""
    prompt: str
    overrides: dict[str, Any] | None = None


class ApprovePlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str | None = None  # if present, replaces plan.approved.md


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _wire_routes(app: FastAPI) -> None:

    @app.post("/api/auth/login")
    async def login(payload: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        cfg = load_global_config()
        if not cfg.auth.enabled or not cfg.auth.passphrase_hash:
            raise HTTPException(status_code=409, detail="auth is not enabled")
        if not verify_passphrase(cfg.auth.passphrase_hash, payload.passphrase):
            raise HTTPException(status_code=401, detail="invalid passphrase")
        token = issue_session(cfg.auth.session_secret or "")
        response.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(request, cfg.server.trusted_hosts),
            max_age=60 * 60 * 24 * 8,
            path="/",
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    async def logout(response: Response) -> dict[str, Any]:
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/bootstrap")
    async def bootstrap(payload: BootstrapIn) -> dict[str, Any]:
        """One-shot setup endpoint. Refuses to run if auth is already configured."""
        cfg = load_global_config()
        if cfg.auth.enabled and cfg.auth.passphrase_hash:
            raise HTTPException(status_code=409, detail="auth already configured")
        cfg = _seed_runtime_defaults_if_empty(cfg)
        cfg.auth = AuthConfig(
            enabled=True,
            passphrase_hash=hash_passphrase(payload.passphrase),
            session_secret=generate_session_secret(),
        )
        write_global_config(cfg)
        return {"ok": True}

    @app.get("/api/auth/status")
    async def auth_status() -> dict[str, Any]:
        cfg = load_global_config()
        return {
            "enabled": cfg.auth.enabled,
            "bootstrap_required": cfg.auth.enabled is False,
        }

    # ----- Dashboard ----------------------------------------------------

    @app.get("/api/dashboard")
    async def dashboard(_: SessionUser = Depends(require_user)) -> dict[str, Any]:
        cfg = load_global_config()
        runs: list[dict[str, Any]] = []
        for project in cfg.projects:
            project_path = Path(project.path)
            for record in DEFAULT_RUN_STORE.discover(project_path):
                runs.append(_run_summary(record))
        runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return {"runs": runs[:200]}

    # ----- Projects ----------------------------------------------------

    @app.get("/api/projects")
    async def projects(_: SessionUser = Depends(require_user)) -> list[ProjectOut]:
        out: list[ProjectOut] = []
        for info in list_projects():
            out.append(
                ProjectOut(
                    name=info.name,
                    path=str(info.path),
                    has_kajas_dir=info.has_kajas_dir,
                    is_git=info.is_git,
                    config=info.config.model_dump(mode="json"),
                )
            )
        return out

    @app.post("/api/projects/select-directory")
    async def select_project_directory(
        _: SessionUser = Depends(require_user),
    ) -> dict[str, str | None]:
        try:
            selected = _select_directory()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"path": selected}

    @app.post("/api/projects", status_code=201)
    async def create_project(
        payload: ProjectIn, _: SessionUser = Depends(require_user)
    ) -> ProjectOut:
        try:
            info = bootstrap_project(
                payload.name,
                Path(payload.path).expanduser(),
                create_kajas_dir=payload.create_kajas_dir,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return ProjectOut(
            name=info.name,
            path=str(info.path),
            has_kajas_dir=info.has_kajas_dir,
            is_git=info.is_git,
            config=info.config.model_dump(mode="json"),
        )

    @app.delete("/api/projects/{name}")
    async def delete_project(
        name: str, _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        removed = unregister_project(name)
        if not removed:
            raise HTTPException(status_code=404, detail="project not found")
        return {"ok": True}

    # ----- Config ------------------------------------------------------

    @app.get("/api/config/global")
    async def get_global_config(_: SessionUser = Depends(require_user)) -> dict[str, Any]:
        return load_global_config().model_dump(mode="json")

    @app.put("/api/config/global")
    async def put_global_config(
        payload: OverrideIn, _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        try:
            cfg = GlobalConfig.model_validate(_yaml_loads(payload.yaml))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid config: {exc}")
        write_global_config(cfg)
        return cfg.model_dump(mode="json")

    @app.get("/api/config/project")
    async def get_project_config(
        project: str = Query(...), _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        info = _must_inspect(project)
        return info.config.model_dump(mode="json")

    @app.put("/api/config/project")
    async def put_project_config(
        project: str,
        payload: OverrideIn,
        _: SessionUser = Depends(require_user),
    ) -> dict[str, Any]:
        info = _must_inspect(project)
        try:
            cfg = ProjectConfig.model_validate(_yaml_loads(payload.yaml))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid config: {exc}")
        write_project_config(cfg, info.path)
        return cfg.model_dump(mode="json")

    @app.get("/api/config/merged")
    async def get_merged_config(
        project: str | None = Query(default=None),
        _: SessionUser = Depends(require_user),
    ) -> dict[str, Any]:
        global_cfg = load_global_config()
        if project is None:
            return global_cfg.model_dump(mode="json")
        info = _must_inspect(project)
        raw = load_project_config_raw(info.path)
        merged = merge_configs(global_cfg, raw)
        return merged.model_dump(mode="json")

    # ----- Runs -------------------------------------------------------

    @app.post("/api/runs", status_code=201)
    async def create_run(
        payload: RunIn, request: Request, _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        info = _must_inspect(payload.project)
        orch: Orchestrator = request.app.state.orchestrator
        try:
            handle = orch.create_run(
                project_name=info.name,
                project_path=info.path,
                workflow_name=payload.workflow,
                title=payload.title,
                prompt=payload.prompt,
                overrides=payload.overrides or {},
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        orch.start(handle)
        return _run_summary(handle.record)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, _: SessionUser = Depends(require_user)) -> dict[str, Any]:
        cfg = load_global_config()
        for project in cfg.projects:
            project_path = Path(project.path)
            record = DEFAULT_RUN_STORE.read_record(project_path, run_id)
            if record is None:
                continue
            return _run_summary(record, with_body=True)
        raise HTTPException(status_code=404, detail="run not found")

    @app.get("/api/runs/{run_id}/events/stream")
    async def stream_run_events(
        run_id: str, request: Request, _: SessionUser = Depends(require_user)
    ) -> StreamingResponse:
        cfg = load_global_config()
        for project in cfg.projects:
            project_path = Path(project.path)
            if not DEFAULT_RUN_STORE.exists(project_path, run_id):
                continue
            orch: Orchestrator = request.app.state.orchestrator
            handle = orch.get(run_id)
            if handle is None:
                # Run is on disk but not active; replay stored events.
                async def _replay() -> Any:
                    for line in DEFAULT_RUN_STORE.replay_event_lines(
                        project_path, run_id
                    ):
                        yield f"data: {line}\n\n"
                    yield 'data: {"type":"log","stage":"planning","text":"end of replay"}\n\n'

                return StreamingResponse(_replay(), media_type="text/event-stream")

            async def _live() -> Any:
                q = handle.attach()
                try:
                    # First flush the historical events so a UI that
                    # opens the stream late catches up.
                    for line in DEFAULT_RUN_STORE.replay_event_lines(project_path, run_id):
                        yield f"data: {line}\n\n"
                    while True:
                        if await request.is_disconnected():
                            break
                        try:
                            ev = await asyncio.wait_for(q.get(), timeout=15.0)
                        except asyncio.TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                        payload = ev.model_dump_json()
                        yield f"data: {payload}\n\n"
                        if ev.type in {"final", "error"} and handle.record.status in TERMINAL_STATUSES:
                            break
                finally:
                    handle.detach(q)

            return StreamingResponse(_live(), media_type="text/event-stream")
        raise HTTPException(status_code=404, detail="run not found")

    @app.post("/api/runs/{run_id}/approve-plan")
    async def approve_plan(
        run_id: str,
        payload: ApprovePlanIn,
        request: Request,
        _: SessionUser = Depends(require_user),
    ) -> dict[str, Any]:
        orch: Orchestrator = request.app.state.orchestrator
        handle = orch.get(run_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="run not active")
        try:
            orch.approve_plan(handle, edited_plan=payload.plan)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _run_summary(handle.record)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str, request: Request, _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        orch: Orchestrator = request.app.state.orchestrator
        handle = orch.get(run_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="run not active")
        orch.cancel(run_id)
        return _run_summary(handle.record)

    @app.post("/api/runs/{run_id}/rerun-failed-phase")
    async def rerun_failed_phase(
        run_id: str, request: Request, _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        cfg = load_global_config()
        orch: Orchestrator = request.app.state.orchestrator
        for project in cfg.projects:
            project_path = Path(project.path)
            if not DEFAULT_RUN_STORE.exists(project_path, run_id):
                continue
            try:
                handle = orch.rerun_failed_phase(
                    project_path=project_path,
                    run_id=run_id,
                    global_config=cfg,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="run not found")
            return _run_summary(handle.record)
        raise HTTPException(status_code=404, detail="run not found")

    @app.delete("/api/runs/{run_id}")
    async def delete_run(
        run_id: str, request: Request, _: SessionUser = Depends(require_user)
    ) -> dict[str, Any]:
        orch: Orchestrator = request.app.state.orchestrator
        handle = orch.get(run_id)
        if handle is not None:
            try:
                orch.delete(run_id)
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return {"ok": True}

        cfg = load_global_config()
        for project in cfg.projects:
            project_path = Path(project.path)
            record = DEFAULT_RUN_STORE.read_record(project_path, run_id)
            if record is None:
                continue
            if record.status not in TERMINAL_STATUSES + ("interrupted", "draft"):
                raise HTTPException(
                    status_code=409,
                    detail=f"cannot delete run in status {record.status!r}",
                )
            DEFAULT_RUN_STORE.delete(project_path, run_id)
            return {"ok": True}
        return {"ok": True}

    # ----- Health ----------------------------------------------------

    @app.get("/api/health")
    async def health(_: SessionUser = Depends(require_user)) -> dict[str, Any]:
        return summarize(run_basic_checks())

    @app.post("/api/health/tool-smoke")
    async def tool_smoke(_: SessionUser = Depends(require_user)) -> dict[str, Any]:
        return summarize(run_tool_smoke())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yaml_loads(text: str) -> dict[str, Any]:
    import yaml

    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("config must be a YAML mapping")
    return data


def _seed_runtime_defaults_if_empty(cfg: GlobalConfig) -> GlobalConfig:
    if cfg.agents or cfg.workflows or cfg.adapters or cfg.tools:
        return cfg
    from .cli import default_global_config

    seeded = default_global_config()
    seeded.server = cfg.server
    seeded.auth = cfg.auth
    seeded.projects = cfg.projects
    return seeded


def _must_inspect(name: str) -> ProjectInfo:
    try:
        return inspect_project(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _select_directory() -> str | None:
    if platform.system() == "Darwin":
        return _select_directory_macos()
    if platform.system() == "Windows":
        return _select_directory_windows()
    selected = _select_directory_linux()
    if selected is not None:
        return selected
    return _select_directory_tk()


def _select_directory_macos() -> str | None:
    script = 'POSIX path of (choose folder with prompt "Select project directory")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("directory picker is unavailable: osascript not found") from exc
    if result.returncode != 0:
        if "User canceled" in result.stderr:
            return None
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"directory picker failed: {detail}")
    return result.stdout.strip() or None


def _select_directory_windows() -> str | None:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = 'Select project directory'; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ $dialog.SelectedPath }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("directory picker is unavailable: PowerShell not found") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"directory picker failed: {detail}")
    return result.stdout.strip() or None


def _select_directory_linux() -> str | None:
    commands = (
        ["zenity", "--file-selection", "--directory"],
        ["kdialog", "--getexistingdirectory"],
    )
    for command in commands:
        if not shutil.which(command[0]):
            continue
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip() or None
        if result.returncode == 1:
            return None
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"directory picker failed: {detail}")
    return None


def _select_directory_tk() -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"directory picker is unavailable: {exc}") from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select project directory")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"directory picker failed: {exc}") from exc
    finally:
        if root is not None:
            root.destroy()

    return selected or None


def _run_summary(
    record: RunRecord,
    *,
    with_body: bool = False,
    store: RunStore = DEFAULT_RUN_STORE,
) -> dict[str, Any]:
    usage = {k: v.model_dump(mode="json") if v else None for k, v in record.usage.items()}
    total = sum((u.get("total_tokens") or 0) for u in usage.values() if u)
    out: dict[str, Any] = {
        "id": record.id,
        "project": record.project_name,
        "project_path": record.project_path,
        "title": record.title,
        "status": record.status,
        "workflow": record.workflow,
        "started_at": record.started_at,
        "updated_at": record.updated_at,
        "usage": usage,
        "total_tokens": total or None,
        "planner_agent": record.planner_agent,
        "implementor_agent": record.implementor_agent,
        "plan_approved_at": record.plan_approved_at,
        "error": record.error,
    }
    if with_body:
        out["prompt"] = record.prompt
        out["effective_config"] = record.effective_config
        out["final_summary"] = record.final_summary
        plan, approved_plan = store.plan_texts(Path(record.project_path), record.id)
        out["plan"] = plan
        out["approved_plan"] = approved_plan
    return out


# Static files mounted last so it doesn't shadow /api/*.
def mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Serve the built Vite ``dist/`` directory and route every non-API
    GET to ``index.html`` so the SPA can handle client-side routing.

    The naive ``app.mount("/", StaticFiles(...))`` would shadow the
    ``/api/*`` routes because it matches first. Instead we mount the
    static directory at ``/_static`` and add a catch-all that only
    fires for non-API paths.
    """
    from fastapi.responses import FileResponse

    if not dist_dir.exists():
        return
    app.mount(
        "/_static", StaticFiles(directory=str(dist_dir), html=False), name="static"
    )

    index_path = dist_dir / "index.html"
    if not index_path.exists():
        return

    @app.get("/", include_in_schema=False)
    async def _root() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa_fallback(path: str) -> FileResponse:
        # Never shadow the API or the static mount.
        if path.startswith("api/") or path.startswith("_static/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="not found")
        candidate = dist_dir / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_path)
