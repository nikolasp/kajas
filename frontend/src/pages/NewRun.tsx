import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

export function NewRun() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [projects, setProjects] = useState<any[]>([]);
  const [merged, setMerged] = useState<any | null>(null);
  const [project, setProject] = useState<string>(params.get("project") || "");
  const [workflow, setWorkflow] = useState<string>("default");
  const [title, setTitle] = useState<string>("");
  const [prompt, setPrompt] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.projects().then(setProjects).catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    if (!project) {
      setMerged(null);
      return;
    }
    api
      .mergedConfig(project)
      .then(setMerged)
      .catch((e) => setError(e.detail));
  }, [project]);

  const workflowOptions = useMemo(
    () => Object.keys(merged?.workflows || {}),
    [merged],
  );
  useEffect(() => {
    if (workflowOptions.length && !workflowOptions.includes(workflow)) {
      setWorkflow(workflowOptions[0]);
    }
  }, [workflowOptions, workflow]);

  const wf = merged?.workflows?.[workflow];
  const gates = wf ? merged?.approval_gate_sets?.[wf.approval_gate_set] : null;
  const planner = wf ? merged?.agents?.[wf.planner] : null;
  const implementor = wf ? merged?.agents?.[wf.implementor] : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const run = await api.createRun({ project, workflow, title, prompt });
      navigate(`/runs/${encodeURIComponent(run.id)}`);
    } catch (err: any) {
      setError(err.detail || "Failed to start run");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold">New Run</h1>
        <p className="text-sm text-ink-400">
          Pick a project and a workflow, write a prompt, and start. The
          effective config is snapshotted when the run begins.
        </p>
      </header>

      <form onSubmit={submit} className="panel">
        <div className="panel-body grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="label">project</span>
            <select
              required
              className="input mt-1"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="" disabled>
                Choose…
              </option>
              {projects.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">workflow</span>
            <select
              className="input mt-1"
              value={workflow}
              onChange={(e) => setWorkflow(e.target.value)}
            >
              {workflowOptions.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </label>
          <label className="block sm:col-span-2">
            <span className="label">title</span>
            <input
              className="input mt-1"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Add OAuth login"
            />
          </label>
          <label className="block sm:col-span-2">
            <span className="label">task prompt</span>
            <textarea
              required
              className="input mt-1 h-48 resize-y font-mono text-xs"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe what you want done. The planner will inspect the repo and produce an implementation brief; the implementor will apply it after approval."
            />
          </label>
        </div>

        {merged && (
          <div className="border-t border-ink-700 bg-ink-900/40 px-5 py-3 text-xs text-ink-300">
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <span className="label">planner</span>{" "}
                <span className="font-mono text-ink-200">
                  {planner ? `${planner.tool} / ${planner.model}` : "—"}
                </span>
              </div>
              <div>
                <span className="label">implementor</span>{" "}
                <span className="font-mono text-ink-200">
                  {implementor ? `${implementor.tool} / ${implementor.model}` : "—"}
                </span>
              </div>
              <div>
                <span className="label">gates</span>{" "}
                <span className="text-ink-200">
                  {gates
                    ? Object.entries(gates)
                        .filter(([k]) => k.startsWith("pause_"))
                        .map(([k, v]) => `${k.replace("pause_", "")}=${v}`)
                        .join(", ")
                    : "—"}
                </span>
              </div>
              <div>
                <span className="label">verification</span>{" "}
                <span className="text-ink-200">
                  {wf?.verification?.commands?.length
                    ? `${wf.verification.commands.length} command(s)`
                    : "none"}
                </span>
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-ink-700 px-5 py-3">
          {error && <span className="mr-auto text-sm text-rose-300">{error}</span>}
          <button className="btn-primary" disabled={busy || !project}>
            {busy ? "Starting…" : "Start Run"}
          </button>
        </div>
      </form>
    </div>
  );
}
