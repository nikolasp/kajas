import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

export function Projects() {
  const [projects, setProjects] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [createKajasDir, setCreateKajasDir] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => api.projects().then(setProjects).catch(() => setProjects([]));
  useEffect(() => {
    reload();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.createProject(name, path, createKajasDir);
      setName("");
      setPath("");
      await reload();
    } catch (err: any) {
      setError(err.detail || "Failed to register project");
    } finally {
      setBusy(false);
    }
  }

  async function choosePath() {
    setError(null);
    setBusy(true);
    try {
      const result = await api.selectProjectDirectory();
      if (result.path) {
        setPath(result.path);
      }
    } catch (err: any) {
      setError(err.detail || "Folder picker is unavailable");
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (!confirm(`Unregister project ${name}? Files on disk will NOT be deleted.`))
      return;
    await api.deleteProject(name);
    await reload();
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header>
        <h1 className="text-2xl font-semibold">Projects</h1>
        <p className="text-sm text-ink-400">
          Register a directory so Kajas can run workflows against it.
        </p>
      </header>

      <section className="panel">
        <div className="panel-header">
          <h2 className="text-sm font-semibold text-ink-200">Register</h2>
        </div>
        <form onSubmit={submit} className="panel-body grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="label">name</span>
            <input
              required
              className="input mt-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-project"
            />
          </label>
          <label className="block">
            <span className="label">path</span>
            <div className="mt-1 flex gap-2">
              <input
                required
                className="input"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/path/to/my-project"
              />
              <button
                type="button"
                className="btn shrink-0"
                disabled={busy}
                onClick={choosePath}
              >
                Choose
              </button>
            </div>
          </label>
          <label className="flex items-center gap-2 sm:col-span-2">
            <input
              type="checkbox"
              checked={createKajasDir}
              onChange={(e) => setCreateKajasDir(e.target.checked)}
            />
            <span className="text-sm text-ink-200">
              Create <code>.kajas/</code> with a starter <code>config.yaml</code>
            </span>
          </label>
          {error && (
            <p className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200 sm:col-span-2">
              {error}
            </p>
          )}
          <div className="sm:col-span-2">
            <button className="btn-primary" disabled={busy}>
              {busy ? "Registering…" : "Register"}
            </button>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="text-sm font-semibold text-ink-200">Registered</h2>
        </div>
        {projects.length === 0 ? (
          <div className="p-5 text-sm text-ink-400">No projects registered.</div>
        ) : (
          projects.map((p) => (
            <div key={p.name} className="flex items-center justify-between border-b border-ink-800 px-5 py-3 text-sm last:border-b-0">
              <div>
                <div className="font-medium text-ink-100">{p.name}</div>
                <div className="text-xs text-ink-400">{p.path}</div>
                <div className="mt-1 flex gap-1.5 text-xs text-ink-400">
                  <span className="badge">
                    {p.is_git ? "git" : "no git"}
                  </span>
                  <span className="badge">
                    {p.has_kajas_dir ? ".kajas/ present" : "no .kajas/"}
                  </span>
                </div>
              </div>
              <div className="flex gap-2">
                <Link
                  to={`/config?project=${encodeURIComponent(p.name)}`}
                  className="btn"
                >
                  Config
                </Link>
                <Link
                  to={`/runs/new?project=${encodeURIComponent(p.name)}`}
                  className="btn"
                >
                  New run
                </Link>
                <button
                  onClick={() => remove(p.name)}
                  className="btn-danger"
                >
                  Unregister
                </button>
              </div>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
