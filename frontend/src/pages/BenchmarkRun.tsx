import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import type { BenchmarkDetail, BenchmarkSummary } from "../lib/types";
import { timeAgo } from "../lib/format";

const WEIGHTS = [
  ["tool_calling", "Tool calling", 25],
  ["context_retrieval", "Context retrieval", 25],
  ["coding", "Coding", 35],
  ["latency_reliability", "Latency", 15],
] as const;

export function BenchmarkRun() {
  const [searchParams] = useSearchParams();
  const [runs, setRuns] = useState<BenchmarkSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("selected"));
  const [detail, setDetail] = useState<BenchmarkDetail | null>(null);
  const [baseUrl, setBaseUrl] = useState("http://localhost:11434/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [headersText, setHeadersText] = useState("");
  const [maxContext, setMaxContext] = useState("32768");
  const [judgeTool, setJudgeTool] = useState<"codex" | "pi">("codex");
  const [judgeModel, setJudgeModel] = useState("gpt-5.5");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => runs.find((run) => run.id === selectedId) || runs[0] || null,
    [runs, selectedId],
  );

  async function reload() {
    const next = await api.benchmarks();
    setRuns(next);
    if (!selectedId && next.length > 0) {
      setSelectedId(next[0].id);
    }
  }

  useEffect(() => {
    reload().catch((e) => setError(e.detail || "Failed to load benchmarks"));
  }, []);

  useEffect(() => {
    if (!selected?.id) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    async function loadDetail() {
      try {
        const next = await api.benchmark(selected.id);
        if (!cancelled) setDetail(next);
      } catch (e: any) {
        if (!cancelled) setError(e.detail || "Failed to load benchmark");
      }
    }
    loadDetail();
    const interval = selected.status === "running"
      ? window.setInterval(() => {
          reload().catch(() => undefined);
          loadDetail();
        }, 2000)
      : undefined;
    return () => {
      cancelled = true;
      if (interval) window.clearInterval(interval);
    };
  }, [selected?.id, selected?.status]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const run = await api.createBenchmark({
        base_url: baseUrl.trim(),
        api_key: apiKey.trim() || null,
        custom_headers: parseHeaders(headersText),
        model: model.trim() || null,
        max_context_tokens: maxContext.trim() ? Number(maxContext) : null,
        coding_judge_tool: judgeTool,
        coding_judge_model: judgeModel.trim() || "gpt-5.5",
      });
      setSelectedId(run.id);
      await reload();
    } catch (e: any) {
      setError(e.detail || "Failed to start benchmark");
    } finally {
      setSubmitting(false);
    }
  }

  function rerun(run: BenchmarkSummary) {
    setBaseUrl(run.base_url);
    setModel(run.configured_model || run.model || "");
    setApiKey("");
    setHeadersText("");
    setMaxContext(String(run.effective_context_window || 32768));
    setJudgeTool(run.coding_judge_tool || "codex");
    setJudgeModel(run.coding_judge_model || "gpt-5.5");
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Run Benchmark</h1>
          <p className="text-sm text-ink-400">
            Evaluate local OpenAI-compatible models for tool use, context retrieval, coding, and reliability.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/benchmark" className="btn">
            Comparison
          </Link>
          <button className="btn" onClick={() => reload()} disabled={submitting}>
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[24rem_minmax(0,1fr)]">
        <section className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">New run</h2>
            <span className="badge">70 usable</span>
          </div>
          <form className="panel-body space-y-4" onSubmit={submit}>
            <label className="block space-y-1">
              <span className="label">Base API URL</span>
              <input
                className="input"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:11434/v1"
                required
              />
            </label>
            <label className="block space-y-1">
              <span className="label">API key</span>
              <input
                className="input"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Optional"
              />
            </label>
            <label className="block space-y-1">
              <span className="label">Model override</span>
              <input
                className="input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="First /models entry"
              />
            </label>
            <label className="block space-y-1">
              <span className="label">Max context tokens</span>
              <input
                className="input"
                type="number"
                min={1024}
                max={262144}
                value={maxContext}
                onChange={(e) => setMaxContext(e.target.value)}
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-[8rem_minmax(0,1fr)]">
              <label className="block space-y-1">
                <span className="label">Judge</span>
                <select
                  className="input"
                  value={judgeTool}
                  onChange={(e) => setJudgeTool(e.target.value as "codex" | "pi")}
                >
                  <option value="codex">Codex</option>
                  <option value="pi">Pi</option>
                </select>
              </label>
              <label className="block space-y-1">
                <span className="label">Judge model</span>
                <input
                  className="input"
                  value={judgeModel}
                  onChange={(e) => setJudgeModel(e.target.value)}
                  placeholder="gpt-5.5"
                />
              </label>
            </div>
            <label className="block space-y-1">
              <span className="label">Custom headers</span>
              <textarea
                className="input min-h-24 font-mono"
                value={headersText}
                onChange={(e) => setHeadersText(e.target.value)}
                placeholder={"X-Header: value\nX-Another: value"}
              />
            </label>
            <button className="btn-primary w-full justify-center" disabled={submitting}>
              {submitting ? "Starting..." : "Run benchmark"}
            </button>
          </form>
        </section>

        <section className="space-y-6">
          <ScoreBoard run={detail || selected} />

          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(19rem,0.9fr)]">
            <section className="panel overflow-hidden">
              <div className="panel-header">
                <h2 className="text-sm font-semibold text-ink-200">Tests</h2>
                {detail && <span className="badge">{detail.tests.length} checks</span>}
              </div>
              {!detail ? (
                <div className="p-5 text-sm text-ink-400">No benchmark selected.</div>
              ) : (
                <div className="divide-y divide-ink-800">
                  {detail.tests.map((test) => (
                    <div key={test.name} className="grid gap-3 px-5 py-3 text-sm md:grid-cols-[1fr_5rem]">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={`status-dot ${test.ok ? "completed" : "failed"}`} />
                          <span className="font-mono text-ink-100">{test.name}</span>
                        </div>
                        <p className="mt-1 break-words text-xs text-ink-400">{String(test.detail || "")}</p>
                      </div>
                      <div className="text-right font-mono text-ink-200">{Number(test.score || 0).toFixed(2)}</div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel overflow-hidden">
              <div className="panel-header">
                <h2 className="text-sm font-semibold text-ink-200">History</h2>
                <span className="badge">{runs.length}</span>
              </div>
              <div className="max-h-[34rem] divide-y divide-ink-800 overflow-y-auto">
                {runs.length === 0 ? (
                  <div className="p-5 text-sm text-ink-400">Run a benchmark to create history.</div>
                ) : (
                  runs.map((run) => (
                    <button
                      key={run.id}
                      onClick={() => setSelectedId(run.id)}
                      className={[
                        "block w-full px-5 py-3 text-left transition",
                        selected?.id === run.id ? "bg-accent-600/10" : "hover:bg-ink-800/60",
                      ].join(" ")}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-medium text-ink-100">
                          {run.model || run.configured_model || "Detecting model"}
                        </span>
                        <span className={scoreBadge(run)}>{run.status === "running" ? "running" : `${run.total_score.toFixed(1)}`}</span>
                      </div>
                      <div className="mt-1 truncate text-xs text-ink-400">{run.base_url}</div>
                      <div className="mt-2 flex items-center justify-between text-xs text-ink-500">
                        <span>{timeAgo(run.created_at)}</span>
                        <span
                          role="button"
                          tabIndex={0}
                          className="text-accent-400 hover:text-accent-300"
                          onClick={(e) => {
                            e.stopPropagation();
                            rerun(run);
                          }}
                        >
                          rerun config
                        </span>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </section>
          </div>

          {detail && (
            <section className="panel overflow-hidden">
              <div className="panel-header">
                <h2 className="text-sm font-semibold text-ink-200">Raw responses</h2>
                <span className="badge">{detail.raw.length}</span>
              </div>
              <details className="panel-body">
                <summary className="cursor-pointer text-sm text-ink-300">Show stored request and response data</summary>
                <pre className="event-json mt-4 max-h-[32rem] overflow-auto rounded-lg bg-ink-900/80 p-4">
                  {JSON.stringify(detail.raw, null, 2)}
                </pre>
              </details>
            </section>
          )}
        </section>
      </div>
    </div>
  );
}

function ScoreBoard({ run }: { run: BenchmarkSummary | BenchmarkDetail | null }) {
  if (!run) {
    return (
      <section className="panel p-6 text-sm text-ink-400">
        No benchmark history yet.
      </section>
    );
  }
  return (
    <section className="panel overflow-hidden">
      <div className="grid gap-0 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <div className="border-b border-ink-700 p-5 lg:border-b-0 lg:border-r lg:p-6">
          <div className="text-xs uppercase tracking-wider text-ink-400">{run.status}</div>
          <div className="mt-2 font-mono text-5xl font-semibold text-ink-50">
            {run.total_score.toFixed(1)}
          </div>
          <div className="mt-2 text-sm text-ink-400">of 100</div>
          <div className="mt-4">
            <span className={scoreBadge(run)}>
              {run.status === "running" ? "running" : run.usable ? "usable" : "below threshold"}
            </span>
          </div>
        </div>
        <div className="min-w-0 p-5 lg:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold text-ink-100">
                {run.model || run.configured_model || "Detecting model"}
              </h2>
              <p className="mt-1 break-all text-sm text-ink-400">{run.base_url}</p>
            </div>
            <div className="text-right text-xs text-ink-400">
              <div>{timeAgo(run.updated_at)}</div>
              {run.effective_context_window && (
                <div className="mt-1 font-mono">{run.effective_context_window.toLocaleString()} ctx</div>
              )}
              <div className="mt-1 font-mono">
                {run.coding_judge_tool}/{run.coding_judge_model}
              </div>
            </div>
          </div>
          {run.error && (
            <div className="mt-4 rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {run.error}
            </div>
          )}
          <div className="mt-5 space-y-2">
            {WEIGHTS.map(([key, label, max]) => {
              const value = run.scores[key] || 0;
              const pct = Math.max(0, Math.min(100, (value / max) * 100));
              return (
                <div key={key} className="rounded-lg border border-ink-700 bg-ink-900/40 px-3 py-2">
                  <div className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-4 text-xs">
                    <span className="min-w-0 truncate text-ink-300" title={label}>{label}</span>
                    <span className="whitespace-nowrap text-right font-mono text-ink-200">
                      {value.toFixed(1)}/{max}
                    </span>
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-800">
                    <div className="h-full bg-accent-500" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

function parseHeaders(text: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf(":");
    if (idx <= 0) continue;
    headers[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return headers;
}

function scoreBadge(run: BenchmarkSummary | BenchmarkDetail) {
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
