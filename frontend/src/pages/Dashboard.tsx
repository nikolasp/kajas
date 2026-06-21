import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { StatusPill } from "../components/StatusPill";
import { timeAgo } from "../lib/format";

export function Dashboard() {
  const [runs, setRuns] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api
        .dashboard()
        .then((d) => {
          if (!cancelled) {
            setRuns(d.runs || []);
            setError(null);
          }
        })
        .catch((e) => !cancelled && setError(e.detail || "Failed to load"))
        .finally(() => !cancelled && setLoading(false));
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-sm text-ink-400">
            Recent runs across all registered projects.
          </p>
        </div>
        <Link to="/runs/new" className="btn-primary">
          + New Run
        </Link>
      </header>

      {error && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <section className="panel">
        <div className="runs-table-header">
          <div className="col-span-1">status</div>
          <div className="col-span-4">title</div>
          <div className="col-span-2">project</div>
          <div className="col-span-2">workflow</div>
          <div className="col-span-2">stage</div>
          <div className="col-span-1 text-right">tokens</div>
        </div>
        {loading ? (
          <div className="p-5 text-sm text-ink-400">Loading…</div>
        ) : runs.length === 0 ? (
          <div className="p-5 text-sm text-ink-400">
            No runs yet. Register a project and start one.
          </div>
        ) : (
          runs.map((run) => (
            <Link
              to={`/runs/${encodeURIComponent(run.id)}`}
              key={run.id}
              className="runs-table-row no-underline"
            >
              <div className="col-span-1">
                <StatusPill status={run.status} />
              </div>
              <div className="col-span-4 truncate text-ink-100">
                {run.title || run.id}
              </div>
              <div className="col-span-2 truncate text-ink-300">
                {run.project}
              </div>
              <div className="col-span-2 truncate text-ink-300">
                {run.workflow}
              </div>
              <div className="col-span-2 truncate text-ink-300">
                {run.status}
              </div>
              <div className="col-span-1 text-right font-mono text-ink-200">
                {run.total_tokens ?? "—"}
              </div>
            </Link>
          ))
        )}
      </section>
    </div>
  );
}
