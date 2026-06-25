# Kajas

Local-first harness for running agentic coding workflows from a Web UI.
A user picks a project and a workflow, writes a task prompt, reviews
the generated plan when configured to pause, then lets the configured
agent profiles complete the implementation while Kajas records state,
logs, approvals, and token usage in project-local files.

See [`docs/kajas-v1-design.md`](docs/kajas-v1-design.md) for the full
V1 design.

## Stack

- **Backend**: Python 3.11+ / FastAPI / uvicorn.
- **Frontend**: React 18 / Vite / Tailwind CSS / TypeScript.
- **Adapters**: Codex CLI, Pi CLI, plus a built-in `fake` adapter
  for tests and demos.
- **Auth**: Argon2id passphrase + signed session cookie (HttpOnly,
  SameSite=Lax).
- **State**: global YAML config at `~/.config/kajas/config.yaml`,
  per-project config at `<repo>/.kajas/config.yaml`. Per-run
  artifacts under `<repo>/.kajas/runs/<id>/`.

## Quick Start

```bash
# 1. Install backend deps and the kajas package
pip install -e backend/

# 2. Install frontend deps
cd frontend && npm install && cd ..

# 3. Write a starter global config and set a passphrase
kajas init           # writes config.yaml; prompts for a passphrase

# 4. Start the dev server (backend on :8765, Vite on :5173)
make dev             # or: ./kajas --dev
```

Open <http://127.0.0.1:5173> and sign in with the passphrase you set
in step 3.

Dev server settings can be adjusted with environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAJAS_VITE_HOST` | `0.0.0.0` | Host Vite binds to. |
| `KAJAS_VITE_PORT` | `5173` | Vite dev server port. |
| `KAJAS_VITE_ALLOWED_HOSTS` | unset | Comma-separated host allow-list for Vite. Use `*` to allow any host. |
| `KAJAS_API_PROXY_TARGET` | `http://127.0.0.1:8765` | Backend target for Vite's `/api` proxy. |

For example, to reach Vite through a specific hostname:

```bash
KAJAS_VITE_HOST=0.0.0.0 KAJAS_VITE_ALLOWED_HOSTS=my-host.example.com make dev
```

The starter global config includes the default real workflow:

```yaml
agents:
  planner:
    tool: codex
    model: gpt-5.5
    role: planner
  coder:
    tool: pi
    model: Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf
    role: implementor
    extra:
      local_model: Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf
workflows:
  default:
    planner: planner
    implementor: coder
```

For production-ish use, build the frontend once and serve it from the
FastAPI process:

```bash
cd frontend && npm run build && cd ..
kajas serve --frontend-dir frontend/dist
```

## Desktop App

Kajas has a Tauri v2 desktop wrapper under `frontend/src-tauri/`. The
desktop shell starts the Python backend on localhost, serves the built
Vite UI through that backend, and opens a native webview window against
it.

Prerequisites:

- Rust/Cargo via <https://rustup.rs/>.
- The backend importable from this checkout, or installed as `kajas`.
- The frontend dependencies installed with `npm install` in
  `frontend/`.

Run it from the frontend package:

```bash
cd frontend
npm run desktop:dev
```

Build the desktop binary:

```bash
cd frontend
npm run desktop:build
```

Create Linux packages:

```bash
cd frontend
npm run desktop:bundle:linux-packages  # deb + rpm
npm run desktop:bundle                 # all configured bundles, including AppImage
```

By default the wrapper launches `python3 -m kajas.cli serve` with
`PYTHONPATH` pointed at `../backend`. Override `KAJAS_BACKEND_CMD` to
use another executable, and `KAJAS_DESKTOP_PORT` if port `8765` is not
available.

## CLI

```text
kajas init                  # write starter global config, set passphrase
kajas serve [--host H] [--port P] [--frontend-dir DIR]
kajas init-project NAME PATH [--no-bootstrap-dir]
kajas run --project NAME --workflow NAME --prompt "..." [--delete]
kajas doctor [--tool-smoke | --no-tool-smoke]
```

`kajas run` is a headless, auto-approving version of the Web UI's
"New Run" flow. It is convenient for smoke tests and CI.

## API

The full HTTP/SSE surface is mounted under `/api`:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/auth/login` | sign in with the local passphrase |
| `POST` | `/api/auth/logout` | sign out |
| `POST` | `/api/auth/bootstrap` | first-run passphrase setup |
| `GET`  | `/api/auth/status` | is auth enabled? do we need to bootstrap? |
| `GET`  | `/api/dashboard` | recent runs across all projects |
| `GET`  | `/api/projects` | list registered projects |
| `POST` | `/api/projects` | register a project and bootstrap `.kajas/` |
| `DELETE` | `/api/projects/{name}` | unregister (keeps files) |
| `GET`  | `/api/config/global` | read global config |
| `PUT`  | `/api/config/global` | write global config |
| `GET`  | `/api/config/project?project=…` | read project config |
| `PUT`  | `/api/config/project?project=…` | write project config |
| `GET`  | `/api/config/merged?project=…` | read merged config (read-only) |
| `POST` | `/api/runs` | create + start a run |
| `GET`  | `/api/runs/{id}` | run summary + persisted state |
| `GET`  | `/api/runs/{id}/events/stream` | SSE event stream |
| `POST` | `/api/runs/{id}/approve-plan` | approve (optionally edit) the plan |
| `POST` | `/api/runs/{id}/cancel` | graceful cancel |
| `DELETE` | `/api/runs/{id}` | delete run folder |
| `GET`  | `/api/health` | basic checks |
| `POST` | `/api/health/tool-smoke` | opt-in tool smoke checks |

## Project Layout

```text
backend/kajas/
  cli.py          # argparse CLI
  server.py       # FastAPI app
  auth.py         # argon2 + session cookie
  config.py       # YAML schemas, deep merge, validation
  projects.py     # project registry + bootstrap
  runs.py         # run orchestrator + state machine
  doctor.py       # basic + tool-smoke checks
  adapters/
    base.py       # Adapter / NormalizedEvent / HealthResult
    fake.py       # in-process fake (Milestone 1)
    codex.py      # codex exec --json (Milestone 2)
    pi.py         # pi --mode json (Milestone 2)
frontend/src/
  App.tsx
  main.tsx
  lib/{api,types,format}.ts
  pages/{Dashboard,Projects,Config,NewRun,RunDetail,Health,Login}.tsx
  components/StatusPill.tsx
docs/kajas-v1-design.md
tests/                # pytest suite (config, auth, runs, API)
```

## Tests

```bash
python3 -m pytest tests/
```

The test suite uses a fake workflow and the FastAPI TestClient; it
does not invoke real Codex or Pi. The fake adapter supports hints
embedded in the prompt, e.g. `<!-- kajas:fake mode=fail -->` to
exercise the failure path.

## Milestones

- **M1 (delivered)**: vertical skeleton with fake adapters, full
  config + auth + project model, dashboard / projects / config / new
  run / run detail / health UI, plan-approval gate, cancellation,
  restart-as-interrupted, basic doctor checks.
- **M2 (delivered)**: real Codex and Pi adapters (best-effort
  translation of the tool-specific event formats into
  `NormalizedEvent`).
- **M3 (partial)**: verification command execution and recording,
  plan amendment flow. Resume/rerun from plan or implementation is
  intentionally left as a follow-up.
