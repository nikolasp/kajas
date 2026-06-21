# Kajas V1 Design

Status: draft from grilling session on 2026-06-21.

## Goal

Kajas is a local-first harness for running agentic coding workflows from a user-friendly Web UI. A user selects a project and workflow, writes a task prompt, reviews the generated plan when configured to pause, then lets configured agent profiles complete the implementation while Kajas records state, logs, approvals, and token usage in project-local files.

## Non-Goals

- Hosted SaaS or remote multi-user execution.
- Installer, Docker, systemd, or desktop packaging in v1.
- Auto-commit or auto-push.
- Open-ended chat during implementation.
- Direct management of secret values.
- Full rollback of partial file changes after cancellation or failure.
- Mediating every repo file edit through Kajas.

## Local-First Model

Kajas runs on the user's machine and may be accessed remotely through Tailscale. The server binds to `127.0.0.1` by default. Exposing it on another host, such as `0.0.0.0`, must be explicit config.

Even when accessed through Tailscale, Kajas requires app-level authentication because it can run tools, edit repos, and expose local project state.

## Stack

- Backend: Python + FastAPI.
- Frontend: React + Vite + Tailwind.
- State: global YAML config plus project-local `.kajas` files.
- Live updates: Server-Sent Events or WebSockets.
- Development: one command starts both backend and frontend.
- Production v1: source-run local app, with `kajas serve` serving API and built frontend.

Suggested repository shape:

```text
backend/
  kajas/
    cli.py
    server.py
    auth.py
    config.py
    projects.py
    runs.py
    doctor.py
    adapters/
      base.py
      fake.py
      codex.py
      pi.py
frontend/
  src/
    pages/
    components/
docs/
```

## CLI

The real `kajas` entrypoint should become a CLI:

```bash
kajas serve
kajas init-project /path/to/repo
kajas run --project /path/to/repo --workflow default "task prompt"
kajas doctor --no-tool-smoke
kajas doctor --tool-smoke
```

V1 focuses on the Web UI. Non-Web `kajas run` can exist as a thin path over the same backend services if cheap, but should not distract from the Web workflow.

## Auth

V1 uses one local admin identity, no user accounts.

Global config stores only a passphrase hash and session secret:

```yaml
server:
  host: 127.0.0.1
  port: 8765
  trusted_hosts:
    - localhost
    - 127.0.0.1
  auth:
    enabled: true
    passphrase_hash: "$argon2id$..."
    session_secret: "random-generated-secret"
```

Login uses `POST /api/auth/login` with a passphrase and returns a signed, HttpOnly, SameSite=Lax session cookie.

First-run bootstrap should detect missing auth config and print a setup URL/token in the terminal.

## Source Of Truth

The filesystem is the source of truth.

The Web UI reads and writes config/run files through the API. The server may keep live subprocess state in memory, but every important user-visible decision must be serialized before execution proceeds.

On restart, Kajas scans known projects and reconstructs run history from `.kajas`.

## Config

Use global plus project YAML configs:

```text
~/.config/kajas/config.yaml
target-repo/.kajas/config.yaml
```

Project config overrides global config by key. The UI should show merged config and where values come from. Project-level edits write only to `target-repo/.kajas/config.yaml`; global edits write only to the global config.

Secrets are never stored directly. Config references environment variables or credential provider keys.

Example minimum schema:

```yaml
server:
  host: 127.0.0.1
  port: 8765
  auth:
    enabled: true

projects:
  - name: Kajas
    path: /path/to/repo

tools:
  codex:
    command: codex
    mode: json
    env:
      OPENAI_API_KEY: env:OPENAI_API_KEY
  pi:
    command: pi
    mode: json
    env:
      PI_API_KEY: env:PI_API_KEY

agents:
  planner:
    tool: codex
    model: gpt-5.5
    role: planner
    policy: careful
  coder:
    tool: pi
    model: Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf
    role: implementor
    policy: careful
    allow_unenforced_policy: true
    extra:
      local_model: Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.IQ4_XS.gguf

policies:
  careful:
    network: ask
    destructive_command: ask
    outside_workspace: ask
    allow_unenforced_policy: false

approval_gate_sets:
  default:
    pause_before_implementation: true
    pause_amendment: false
    pause_final_acceptance: false

workflows:
  default:
    planner: planner
    implementor: coder
    approval_gate_set: default
    verification:
      commands: []
      require_clean_worktree: false
      require_final_summary: true
```

Policy values are:

```text
allow | ask | deny
```

Model names are pass-through non-empty strings. Kajas does not maintain a model catalog in v1.

## Projects

Projects are explicitly registered in global config or through the Web UI. Kajas does not recursively scan arbitrary directories.

The Projects UI can bootstrap a project:

- Validate the path exists.
- Accept non-git directories, but warn when a project is not a git repo.
- Optionally create `.kajas/`.
- Optionally write starter `.kajas/config.yaml`.
- Add the project to global config.

## Project Files

Each project stores Kajas state under `.kajas`:

```text
target-repo/
  .kajas/
    config.yaml
    runs/
      2026-06-21-143012-add-oauth-login/
        run.md
        plan.md
        plan.approved.md
        final.md
        approvals.md
        events.ndjson
        prompts/
          planner.md
          implementor.md
        raw/
          codex.jsonl
          pi.jsonl
```

Run folders may be deleted from the UI after completion or whenever the user chooses.

## Run Model

Every run has an explicit `working_dir`. Agent profiles can provide defaults, and the New Run screen can override them.

At run start, Kajas snapshots the effective config into the run. Later config edits do not affect active runs.

Run statuses:

```text
draft
planning
awaiting_plan_approval
implementing
verifying
awaiting_final_acceptance
completed
failed
cancelled
interrupted
```

Use `deleted` only as an API/UI transient after a folder is removed, not as an on-disk status.

Example `run.md`:

````markdown
---
id: 2026-06-21-143012-add-oauth-login
project_path: /path/to/app
status: awaiting_plan_approval
workflow: default
started_at: 2026-06-21T14:30:12+02:00
updated_at: 2026-06-21T14:33:44+02:00
usage:
  planning:
    input_tokens: 123
    output_tokens: 45
    total_tokens: 168
  implementation: null
---

# Add OAuth Login

## Prompt

...

## Effective Config Snapshot

```yaml
...
```

## Timeline

- 14:30:12 Started
- 14:31:03 Planning completed
- 14:33:44 Awaiting plan approval
````

## Workflow

Default v1 workflow:

1. Intake: user prompt, selected workflow, effective policy snapshot.
2. Planning: Codex inspects the repo and writes `plan.md`.
3. Approval: user approves or edits the plan when `pause_before_implementation` is true.
4. Implementation: Pi applies code changes under policy.
5. Verification: implementor runs configured checks or records why they were not run.
6. Summary: Kajas writes `final.md` with files changed, checks run, token usage, and remaining work.

Planner and implementor are separate agent profiles. For v1, use Codex for planning and Pi for implementation.

The planner must produce a structured implementation brief, including a done definition:

```yaml
goal: "Add OAuth login"
repo: "/path/to/app"
constraints:
  - "Keep existing auth routes"
plan:
  - "Inspect auth module"
  - "Add provider callback route"
  - "Add tests"
done_definition:
  - "User can sign in with Google"
  - "Existing password login still works"
risk_notes:
  - "Requires env vars"
```

The accepted `done_definition` is the run contract. The implementor may mark items complete, fail items, or propose amendments. Amendments pause only when `pause_amendment` is enabled.

Keep original planner output and accepted plan separate:

```text
plan.md
plan.approved.md
```

If the user edits the plan before approving, Pi receives `plan.approved.md`.

## Tool Adapters

V1 requires structured CLI modes for all real tools. If structured output is unavailable or cannot be parsed, fail fast.

Codex verified local interface:

```bash
codex exec --json --model "$MODEL" --cd "$WORKING_DIR" -
```

Pi configured interface:

```bash
pi --mode json --model "$MODEL"
```

Prompts and handoffs are materialized as Markdown files before each stage starts and passed through stdin. Avoid giant shell arguments.

Adapters write raw output to `.kajas/runs/<id>/raw/` and normalize tool output into Kajas events:

```json
{"type":"message","stage":"planning","text":"Inspecting repo..."}
{"type":"tool_call","stage":"planning","name":"shell","summary":"rg auth"}
{"type":"approval_request","stage":"implementation","reason":"network"}
{"type":"usage","stage":"planning","input_tokens":1200,"output_tokens":300,"total_tokens":1500}
{"type":"final","stage":"planning","artifact":"plan.md"}
{"type":"error","stage":"implementation","message":"adapter unsupported output"}
```

Adapter interface:

```text
start(stage, run, agent_profile, prompt_path) -> process handle
stream(process) -> normalized events
cancel(process) -> termination result
capabilities() -> enforceable policy fields
doctor(smoke: bool) -> health result
```

Kajas writes only orchestration files under `.kajas`. Codex and Pi inspect/edit actual repo files.

## Policy Enforcement

Every agent profile declares or references an execution policy.

Adapters declare which policy fields they can enforce:

```yaml
adapters:
  codex:
    supports:
      sandbox: true
      approval_policy: true
      working_dir: true
      network_gate: partial
      destructive_gate: partial
  pi:
    supports:
      sandbox: false
      approval_policy: false
      working_dir: true
      network_gate: false
      destructive_gate: false
```

At run start, Kajas compares effective policy to adapter capabilities. If a selected tool cannot enforce a required policy, block the run unless the effective run config explicitly sets `allow_unenforced_policy: true`.

## Token Usage

Adapters include token usage when the underlying tool exposes it. Kajas stores and displays usage per stage and in total.

If a tool does not expose usage, Kajas shows `unknown`. V1 does not estimate cost.

Example:

```yaml
usage:
  planning:
    input_tokens: 12345
    output_tokens: 2200
    total_tokens: 14545
  implementation:
    input_tokens: 18000
    output_tokens: 4100
    total_tokens: 22100
  total_tokens: 36645
```

## Restart And Cancellation

Subprocesses are not recoverable in v1.

On startup, Kajas scans registered projects. Runs in `planning`, `awaiting_plan_approval`, `implementing`, `verifying`, or `awaiting_final_acceptance` become `interrupted` with a restart event. The UI can offer rerun/archive/delete actions.

Cancellation sends a graceful terminate signal to the active tool process, waits briefly, then kills if needed. Kajas marks the run `cancelled` and does not revert repo changes. The UI must warn that the repo may contain partial changes.

## Web UI

V1 screens:

1. Dashboard
2. Projects
3. Config
4. New Run
5. Run Detail
6. Health

Dashboard:

- Shows recent runs across all registered projects.
- Displays status, project, workflow, stage, token totals, and updated time.
- Quick actions: open run, delete run, new run.

Projects:

- Register project path/name.
- Bootstrap `.kajas`.
- Show git/non-git warning.
- Delete project from registry without deleting project files.

Config:

- Tabs for Merged Config, Project Config, Global Config.
- Raw YAML editor with validation.
- Merged config is read-only.

New Run:

- Select project and workflow.
- Write task prompt.
- Show inherited approval gates and policy.
- Allow per-run overrides before start.
- Freeze effective config at run start.

Run Detail:

- Timeline of normalized events.
- Live tool output.
- Plan display/edit/approval.
- Token usage by stage.
- Final summary.
- Cancel/delete actions.

Health:

- Run basic checks.
- Run optional tool smoke checks with clear token-usage warning.

No open-ended chat during implementation. User input occurs at explicit gates.

## API Sketch

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/dashboard
GET  /api/projects
POST /api/projects
DELETE /api/projects/{project_id}
GET  /api/config/global
PUT  /api/config/global
GET  /api/config/project?project=...
PUT  /api/config/project
GET  /api/config/merged?project=...
POST /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/events/stream
POST /api/runs/{run_id}/approve-plan
POST /api/runs/{run_id}/cancel
DELETE /api/runs/{run_id}
GET  /api/health
POST /api/health/tool-smoke
```

## Doctor

`kajas doctor` and the Web UI Health page share the same backend checks.

Basic checks:

- Global config exists and is parseable.
- Passphrase auth is configured if enabled.
- Registered project paths exist.
- Project `.kajas/config.yaml` files parse.
- Required tool commands are on `PATH`.
- Adapter capability compatibility for each workflow.
- Write access to each project `.kajas`.

Tool smoke checks are opt-in because they may consume tokens:

```bash
kajas doctor --tool-smoke
```

## Milestones

### Milestone 1: Vertical Skeleton With Fake Adapters

- FastAPI backend.
- React/Vite/Tailwind frontend.
- Passphrase auth.
- Global + project YAML loading and merging.
- Project registry and project bootstrap.
- Dashboard across registered projects.
- Config editor with validation.
- New Run flow.
- Run creation that writes `.kajas/runs/<id>/`.
- Fake planner/implementor adapters behind the same adapter interface as real tools.
- Plan approval gate with `plan.md` and `plan.approved.md`.
- Run detail timeline with token counts.
- Cancellation and interrupted-on-restart behavior.
- `kajas doctor` basic checks.

Fake adapters simulate normalized events, usage, failures, amendments, and cancellation. They must be first-class test doubles, not UI-only mocks.

### Milestone 2: Real Tool Adapters

- Codex adapter using `codex exec --json`.
- Pi adapter using `pi --mode json`.
- Raw output capture.
- Normalized event parsing.
- Adapter capability checks.
- Optional tool smoke checks.

### Milestone 3: Workflow Hardening

- Verification command execution and recording.
- Better plan amendment flow.
- Resume/rerun from plan or implementation after interruption.
- More structured validation errors in the config editor.
