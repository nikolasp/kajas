import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import type { BenchmarkSummary } from "../lib/types";
import { timeAgo } from "../lib/format";

export function Benchmark() {
  const [runs, setRuns] = useState<BenchmarkSummary[]>([]);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<BenchmarkSummary | null>(null);

  async function reload() {
    const next = await api.benchmarks();
    setRuns(next);
    setCompareIds((current) => {
      const existing = current.filter((id) => next.some((run) => run.id === id));
      if (existing.length > 0) return existing;
      return next
        .filter((run) => run.status === "completed")
        .slice(0, 4)
        .map((run) => run.id);
    });
  }

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        await reload();
        if (!cancelled) setError(null);
      } catch (e: any) {
        if (!cancelled) setError(e.detail || "Failed to load benchmarks");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    tick();
    const interval = window.setInterval(tick, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const stats = useMemo(() => {
    const completed = runs.filter((run) => run.status === "completed");
    const best = completed.reduce<BenchmarkSummary | null>(
      (current, run) => (!current || run.total_score > current.total_score ? run : current),
      null,
    );
    const averageScore = completed.length
      ? completed.reduce((sum, run) => sum + run.total_score, 0) / completed.length
      : 0;
    const completedDurations = completed
      .map((run) => elapsedMs(run))
      .filter((value): value is number => value !== null);
    const averageDuration = completedDurations.length
      ? completedDurations.reduce((sum, value) => sum + value, 0) / completedDurations.length
      : null;

    return {
      total: runs.length,
      completed: completed.length,
      running: runs.filter((run) => run.status === "running").length,
      best,
      averageScore,
      averageDuration,
    };
  }, [runs]);

  const comparedRuns = useMemo(
    () => compareIds
      .map((id) => runs.find((run) => run.id === id))
      .filter((run): run is BenchmarkSummary => Boolean(run)),
    [compareIds, runs],
  );

  function toggleCompare(id: string) {
    setCompareIds((current) =>
      current.includes(id)
        ? current.filter((item) => item !== id)
        : [...current, id].slice(-6),
    );
  }

  function requestDelete(run: BenchmarkSummary) {
    if (run.status === "running") return;
    setPendingDelete(run);
  }

  async function confirmDelete() {
    const run = pendingDelete;
    if (!run || run.status === "running") return;

    setDeletingId(run.id);
    setError(null);
    try {
      await api.deleteBenchmark(run.id);
      setRuns((current) => current.filter((item) => item.id !== run.id));
      setCompareIds((current) => current.filter((id) => id !== run.id));
      setPendingDelete(null);
    } catch (e: any) {
      setError(e.detail || "Failed to delete benchmark");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Benchmark</h1>
          <p className="text-sm text-ink-400">
            Compare saved local model benchmark runs by score, runtime, model, and context size.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn" onClick={() => reload()} disabled={loading}>
            Refresh
          </button>
          <Link to="/benchmark/run" className="btn-primary">
            Run Benchmark
          </Link>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Metric label="Saved runs" value={String(stats.total)} detail={`${stats.running} running`} />
        <Metric label="Completed" value={String(stats.completed)} detail="available for comparison" />
        <Metric label="Average score" value={stats.completed ? stats.averageScore.toFixed(1) : "0.0"} detail="of 100" />
        <Metric
          label="Average time"
          value={formatDuration(stats.averageDuration)}
          detail={stats.best ? `best: ${modelName(stats.best)}` : "no completed runs"}
        />
      </section>

      {stats.best && (
        <section className="panel overflow-hidden">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">Best run</h2>
            <span className={scoreBadge(stats.best)}>{stats.best.total_score.toFixed(1)}</span>
          </div>
          <div className="grid gap-4 p-5 text-sm md:grid-cols-[minmax(0,1.5fr)_repeat(3,minmax(8rem,0.7fr))]">
            <div className="min-w-0">
              <div className="truncate text-base font-semibold text-ink-100">{modelName(stats.best)}</div>
              <div className="mt-1 break-all text-xs text-ink-400">{stats.best.base_url}</div>
            </div>
            <InlineMetric label="Context" value={formatContext(stats.best)} />
            <InlineMetric label="Time" value={formatDuration(elapsedMs(stats.best))} />
            <InlineMetric label="Updated" value={timeAgo(stats.best.updated_at)} />
          </div>
        </section>
      )}

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <h2 className="text-sm font-semibold text-ink-200">Comparison</h2>
          <span className="badge">{comparedRuns.length} selected</span>
        </div>
        {comparedRuns.length === 0 ? (
          <div className="p-5 text-sm text-ink-400">Select saved runs from history to compare models.</div>
        ) : (
          <div className="overflow-x-auto">
            <div className="grid min-w-[54rem] grid-cols-[minmax(16rem,1.6fr)_7rem_8rem_8rem_8rem_8rem] gap-4 border-b border-ink-700 px-5 py-2 text-xs font-semibold uppercase tracking-wider text-ink-400">
              <div>model</div>
              <div className="text-right">score</div>
              <div className="text-right">time</div>
              <div className="text-right">context</div>
              <div className="text-right">tooling</div>
              <div className="text-right">coding</div>
            </div>
            {comparedRuns.map((run) => (
              <Link
                key={run.id}
                to={`/benchmark/run?selected=${encodeURIComponent(run.id)}`}
                className="grid min-w-[54rem] grid-cols-[minmax(16rem,1.6fr)_7rem_8rem_8rem_8rem_8rem] gap-4 border-b border-ink-800 px-5 py-3 text-sm no-underline hover:bg-ink-800/40"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-ink-100">{modelName(run)}</div>
                  <div className="truncate text-xs text-ink-400">{run.base_url}</div>
                </div>
                <div className="text-right font-mono text-ink-100">{run.total_score.toFixed(1)}</div>
                <div className="text-right font-mono text-ink-200">{formatDuration(elapsedMs(run))}</div>
                <div className="text-right font-mono text-ink-200">{formatContext(run)}</div>
                <div className="text-right font-mono text-ink-200">{formatScore(run.scores.tool_calling)}</div>
                <div className="text-right font-mono text-ink-200">{formatScore(run.scores.coding)}</div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="panel overflow-hidden">
        <div className="panel-header">
          <h2 className="text-sm font-semibold text-ink-200">Saved runs</h2>
          <span className="badge">{runs.length}</span>
        </div>
        <div className="overflow-x-auto">
          <div className="grid min-w-[74rem] grid-cols-[4rem_minmax(16rem,1.8fr)_7rem_8rem_8rem_9rem_8rem_7rem] gap-4 border-b border-ink-700 px-5 py-2 text-xs font-semibold uppercase tracking-wider text-ink-400">
            <div>compare</div>
            <div>model</div>
            <div className="text-right">score</div>
            <div className="text-right">time</div>
            <div className="text-right">context</div>
            <div>status</div>
            <div className="text-right">created</div>
            <div className="text-right">actions</div>
          </div>
          {loading ? (
            <div className="p-5 text-sm text-ink-400">Loading...</div>
          ) : runs.length === 0 ? (
            <div className="p-5 text-sm text-ink-400">No benchmark runs saved yet.</div>
          ) : (
            runs.map((run) => (
              <div
                key={run.id}
                className="grid min-w-[74rem] grid-cols-[4rem_minmax(16rem,1.8fr)_7rem_8rem_8rem_9rem_8rem_7rem] items-center gap-4 border-b border-ink-800 px-5 py-3 text-sm"
              >
                <div>
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-ink-700 bg-ink-900 text-accent-500 focus:ring-accent-500"
                    checked={compareIds.includes(run.id)}
                    onChange={() => toggleCompare(run.id)}
                    aria-label={`Compare ${modelName(run)}`}
                  />
                </div>
                <Link
                  to={`/benchmark/run?selected=${encodeURIComponent(run.id)}`}
                  className="min-w-0 no-underline"
                >
                  <div className="truncate font-medium text-ink-100">{modelName(run)}</div>
                  <div className="truncate text-xs text-ink-400">{run.base_url}</div>
                </Link>
                <div className="text-right font-mono text-ink-100">{run.total_score.toFixed(1)}</div>
                <div className="text-right font-mono text-ink-200">{formatDuration(elapsedMs(run))}</div>
                <div className="text-right font-mono text-ink-200">{formatContext(run)}</div>
                <div><span className={statusBadge(run)}>{run.status}</span></div>
                <div className="text-right text-xs text-ink-400">{timeAgo(run.created_at)}</div>
                <div className="text-right">
                  <button
                    type="button"
                    className="btn-danger px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={run.status === "running" || deletingId === run.id}
                    onClick={() => requestDelete(run)}
                  >
                    {deletingId === run.id ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      {pendingDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70 p-4">
          <section className="panel w-full max-w-md overflow-hidden shadow-2xl shadow-ink-950">
            <div className="panel-header">
              <h2 className="text-sm font-semibold text-ink-100">Delete benchmark run?</h2>
            </div>
            <div className="panel-body space-y-4">
              <div className="space-y-2 text-sm">
                <p className="text-ink-300">
                  This saved benchmark run will be permanently removed.
                </p>
                <div className="rounded-lg border border-ink-700 bg-ink-900/70 p-3">
                  <div className="truncate font-medium text-ink-100">{modelName(pendingDelete)}</div>
                  <div className="mt-1 break-all text-xs text-ink-400">{pendingDelete.base_url}</div>
                  <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
                    <InlineMetric label="Score" value={pendingDelete.total_score.toFixed(1)} />
                    <InlineMetric label="Context" value={formatContext(pendingDelete)} />
                    <InlineMetric label="Time" value={formatDuration(elapsedMs(pendingDelete))} />
                  </div>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  className="btn"
                  disabled={deletingId === pendingDelete.id}
                  onClick={() => setPendingDelete(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-danger"
                  disabled={deletingId === pendingDelete.id}
                  onClick={confirmDelete}
                >
                  {deletingId === pendingDelete.id ? "Deleting..." : "Delete run"}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <section className="panel p-5">
      <div className="text-xs font-semibold uppercase tracking-wider text-ink-400">{label}</div>
      <div className="mt-2 truncate font-mono text-3xl font-semibold text-ink-50">{value}</div>
      <div className="mt-1 truncate text-xs text-ink-400">{detail}</div>
    </section>
  );
}

function InlineMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wider text-ink-400">{label}</div>
      <div className="mt-1 truncate font-mono text-ink-100">{value}</div>
    </div>
  );
}

function modelName(run: BenchmarkSummary) {
  return run.model || run.configured_model || "Detecting model";
}

function elapsedMs(run: BenchmarkSummary): number | null {
  const start = Date.parse(run.created_at);
  const end = Date.parse(run.updated_at);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return end - start;
}

function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined) return "n/a";
  const seconds = Math.round(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
}

function formatContext(run: BenchmarkSummary) {
  const value = run.effective_context_window || run.context_window;
  return value ? value.toLocaleString() : "n/a";
}

function formatScore(value: number | undefined) {
  return typeof value === "number" ? value.toFixed(1) : "0.0";
}

function scoreBadge(run: BenchmarkSummary) {
  if (run.status === "running") {
    return "badge border-amber-500/30 text-amber-300";
  }
  if (run.status === "failed") {
    return "badge border-rose-500/30 text-rose-300";
  }
  return run.usable
    ? "badge border-emerald-500/30 text-emerald-300"
    : "badge border-amber-500/30 text-amber-300";
}

function statusBadge(run: BenchmarkSummary) {
  if (run.status === "completed") return scoreBadge(run);
  if (run.status === "running") return "badge border-amber-500/30 text-amber-300";
  return "badge border-rose-500/30 text-rose-300";
}
