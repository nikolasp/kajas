import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { StatusPill } from "../components/StatusPill";
import { TokenUsageChart } from "../components/TokenUsageChart";
import { fmtNumber } from "../lib/format";
import type { RunSummary } from "../lib/types";

export function Dashboard() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
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

  const runStats = {
    total: runs.length,
    completed: runs.filter((run) => run.status === "completed").length,
    cancelled: runs.filter((run) => run.status === "cancelled").length,
    inProgress: runs.filter((run) =>
      [
        "planning",
        "implementing",
        "verifying",
        "awaiting_plan_approval",
        "awaiting_final_acceptance",
      ].includes(run.status),
    ).length,
    failed: runs.filter((run) => run.status === "failed").length,
  };

  return (
    <div className="w-full max-w-7xl space-y-6 p-8 lg:p-10">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Dashboard</h1>
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

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <TokenUsageChart runs={runs} />

        <div className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">Runs</h2>
          </div>
          <div className="panel-body space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-ink-400">Total</span>
              <span className="font-mono text-ink-100">{runStats.total}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-ink-400">Completed</span>
              <span className="font-mono text-ink-100">{runStats.completed}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-ink-400">Cancelled</span>
              <span className="font-mono text-ink-100">{runStats.cancelled}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-ink-400">In progress</span>
              <span className="font-mono text-ink-100">{runStats.inProgress}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-ink-400">Failed</span>
              <span className="font-mono text-ink-100">{runStats.failed}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="panel overflow-x-auto">
        <div className="runs-table-header">
          <div>status</div>
          <div>title</div>
          <div>project</div>
          <div>workflow</div>
          <div>stage</div>
          <div className="text-right">tokens</div>
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
              <div className="min-w-0">
                <StatusPill status={run.status} />
              </div>
              <div className="truncate text-ink-100">
                {run.title || run.id}
              </div>
              <div className="truncate text-ink-300">
                {run.project}
              </div>
              <div className="truncate text-ink-300">
                {run.workflow}
              </div>
              <div className="truncate text-ink-300">
                {run.status}
              </div>
              <div className="text-right font-mono text-ink-200">
                {fmtNumber(run.total_tokens)}
              </div>
            </Link>
          ))
        )}
      </section>
    </div>
  );
}
