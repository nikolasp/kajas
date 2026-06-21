import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

type Tab = "merged" | "project" | "global";

function toYaml(value: any): string {
  // The backend stores YAML; the simplest reliable round-trip is to
  // dump to JSON and let the user edit a human-readable view. The
  // merged config is the only one that is read-only, so the user
  // can copy it. Project and global edits use the same JSON-as-text
  // form here for v1 simplicity.
  return JSON.stringify(value, null, 2);
}

export function Config() {
  const [params, setParams] = useSearchParams();
  const projectName = params.get("project") || "";
  const [tab, setTab] = useState<Tab>(projectName ? "project" : "merged");
  const [globalText, setGlobalText] = useState("");
  const [projectText, setProjectText] = useState("");
  const [mergedText, setMergedText] = useState("");
  const [projects, setProjects] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.projects().then(setProjects).catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    api
      .globalConfig()
      .then((c) => setGlobalText(toYaml(c)))
      .catch((e) => setError(e.detail));
  }, []);

  useEffect(() => {
    if (!projectName) {
      setProjectText("");
      return;
    }
    api
      .projectConfig(projectName)
      .then((c) => setProjectText(toYaml(c)))
      .catch((e) => setError(e.detail));
  }, [projectName]);

  useEffect(() => {
    api
      .mergedConfig(projectName || undefined)
      .then((c) => setMergedText(toYaml(c)))
      .catch((e) => setError(e.detail));
  }, [projectName]);

  async function save() {
    setError(null);
    setSavedAt(null);
    setBusy(true);
    try {
      if (tab === "global") {
        await api.putGlobalConfig(globalText);
      } else if (tab === "project" && projectName) {
        await api.putProjectConfig(projectName, projectText);
      }
      setSavedAt(new Date().toLocaleTimeString());
    } catch (err: any) {
      setError(err.detail || "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-8">
      <header>
        <h1 className="text-2xl font-semibold">Config</h1>
        <p className="text-sm text-ink-400">
          The merged view is read-only. Project edits go to{" "}
          <code>target-repo/.kajas/config.yaml</code>; global edits go to{" "}
          <code>~/.config/kajas/config.yaml</code>.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <label className="block">
          <span className="label">project</span>
          <select
            className="input mt-1"
            value={projectName}
            onChange={(e) => {
              const v = e.target.value;
              setParams(v ? { project: v } : {});
              if (v) setTab("project");
            }}
          >
            <option value="">(global only)</option>
            {projects.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <div className="ml-auto flex gap-1 rounded-lg border border-ink-700 bg-ink-800 p-0.5">
          {(["merged", "project", "global"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1 text-sm ${
                tab === t
                  ? "bg-accent-600/20 text-accent-400"
                  : "text-ink-300 hover:text-ink-100"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      {tab === "merged" && (
        <div className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">
              Merged Config{projectName ? ` — ${projectName}` : ""}
            </h2>
            <span className="text-xs text-ink-400">read-only</span>
          </div>
          <textarea
            readOnly
            value={mergedText}
            className="h-[60vh] w-full resize-none bg-transparent p-5 font-mono text-xs text-ink-200 focus:outline-none"
          />
        </div>
      )}

      {tab === "global" && (
        <div className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">Global Config</h2>
            <div className="flex items-center gap-2">
              {savedAt && (
                <span className="text-xs text-emerald-300">saved {savedAt}</span>
              )}
              <button className="btn-primary" onClick={save} disabled={busy}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          <textarea
            value={globalText}
            onChange={(e) => setGlobalText(e.target.value)}
            className="h-[60vh] w-full resize-none bg-transparent p-5 font-mono text-xs text-ink-200 focus:outline-none"
          />
        </div>
      )}

      {tab === "project" && (
        <div className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">
              Project Config{projectName ? ` — ${projectName}` : ""}
            </h2>
            <div className="flex items-center gap-2">
              {savedAt && (
                <span className="text-xs text-emerald-300">saved {savedAt}</span>
              )}
              <button
                className="btn-primary"
                onClick={save}
                disabled={busy || !projectName}
              >
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          {projectName ? (
            <textarea
              value={projectText}
              onChange={(e) => setProjectText(e.target.value)}
              className="h-[60vh] w-full resize-none bg-transparent p-5 font-mono text-xs text-ink-200 focus:outline-none"
            />
          ) : (
            <div className="p-5 text-sm text-ink-400">
              Pick a project above to edit its config.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
