import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import type { BenchmarkDetail, BenchmarkSummary } from "../lib/types";
import { timeAgo } from "../lib/format";

const WEIGHTS = [
  ["tool_calling", "Tool calling", 50],
  ["context_retrieval", "Context retrieval", 25],
  ["coding", "Coding", 10],
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
  const [cancelingId, setCancelingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newRunOpen, setNewRunOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

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
      setNewRunOpen(false);
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
    setNewRunOpen(true);
  }

  async function cancelRun(run: BenchmarkSummary | BenchmarkDetail) {
    if (run.status !== "running") return;

    setCancelingId(run.id);
    setError(null);
    try {
      const updated = await api.cancelBenchmark(run.id);
      setDetail(updated);
      await reload();
    } catch (e: any) {
      setError(e.detail || "Failed to cancel benchmark");
    } finally {
      setCancelingId(null);
    }
  }

  async function deleteRun(run: BenchmarkSummary | BenchmarkDetail) {
    if (run.status === "running") return;

    setDeletingId(run.id);
    setError(null);
    try {
      await api.deleteBenchmark(run.id);
      const nextRuns = runs.filter((item) => item.id !== run.id);
      setRuns(nextRuns);
      if (selectedId === run.id) {
        setSelectedId(nextRuns[0]?.id || null);
        setDetail(null);
      }
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
          <h1 className="text-2xl font-semibold">Run Benchmark</h1>
          <p className="text-sm text-ink-400">
            Evaluate local OpenAI-compatible models for tool use, context retrieval, coding, and reliability.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {selected?.status === "running" && (
            <button
              className="btn-danger"
              onClick={() => cancelRun(selected)}
              disabled={cancelingId === selected.id}
            >
              {cancelingId === selected.id ? "Canceling..." : "Cancel run"}
            </button>
          )}
          {selected && selected.status !== "running" && (
            <button
              className="btn-danger"
              onClick={() => deleteRun(selected)}
              disabled={deletingId === selected.id}
            >
              {deletingId === selected.id ? "Deleting..." : "Delete run"}
            </button>
          )}
          <button className="btn-primary" onClick={() => setNewRunOpen(true)}>
            New run
          </button>
          <button
            className="btn lg:hidden"
            onClick={() => setHistoryOpen(true)}
            disabled={submitting}
          >
            History
          </button>
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

      <div className="space-y-6">
        <section className="space-y-6">
          <ScoreBoard run={detail || selected} />

          <section className="panel overflow-hidden">
            <div className="panel-header">
              <h2 className="text-sm font-semibold text-ink-200">Tests</h2>
              {detail && <span className="badge">{detail.tests.length} checks</span>}
              <button
                className="btn"
                onClick={() => setHistoryOpen(true)}
                disabled={submitting}
              >
                History ({runs.length})
              </button>
            </div>
            {!detail ? (
              <div className="p-5 text-sm text-ink-400">No benchmark selected.</div>
            ) : (
              <div className="divide-y divide-ink-800">
                {detail.tests.map((test) => (
                  <TestRow key={test.name} test={test} raw={detail.raw} />
                ))}
              </div>
            )}
          </section>

          <button
            className="btn w-full lg:hidden"
            onClick={() => setHistoryOpen(true)}
            disabled={submitting}
          >
            View history ({runs.length} runs)
          </button>

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

      {historyOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink-900/80 px-4 py-8 backdrop-blur-sm">
          <button
            className="absolute inset-0 h-full w-full cursor-default"
            aria-label="Close history dialog"
            onClick={() => setHistoryOpen(false)}
          />
          <section
            role="dialog"
            aria-modal="true"
            className="panel relative w-full max-w-2xl overflow-hidden"
          >
            <div className="panel-header">
              <h2 className="text-sm font-semibold text-ink-100">History</h2>
              <button className="btn" onClick={() => setHistoryOpen(false)}>Close</button>
            </div>
            <div className="panel-body max-h-[34rem] divide-y divide-ink-800 overflow-y-auto">
              {runs.length === 0 ? (
                <div className="p-5 text-sm text-ink-400">Run a benchmark to create history.</div>
              ) : (
                runs.map((run) => (
                  <button
                    key={run.id}
                    onClick={() => {
                      setSelectedId(run.id);
                      setHistoryOpen(false);
                    }}
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
                    <div className="mt-2 flex items-center justify-between gap-3 text-xs text-ink-500">
                      <span>{timeAgo(run.created_at)}</span>
                      <div className="flex items-center gap-3">
                        {run.status === "running" ? (
                          <span
                            role="button"
                            tabIndex={0}
                            className="text-rose-300 hover:text-rose-200"
                            onClick={(e) => {
                              e.stopPropagation();
                              cancelRun(run);
                            }}
                          >
                            {cancelingId === run.id ? "canceling..." : "cancel"}
                          </span>
                        ) : (
                          <span
                            role="button"
                            tabIndex={0}
                            className="text-rose-300 hover:text-rose-200"
                            onClick={(e) => {
                              e.stopPropagation();
                              deleteRun(run);
                            }}
                          >
                            {deletingId === run.id ? "deleting..." : "delete"}
                          </span>
                        )}
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
                    </div>
                  </button>
                ))
              )}
            </div>
          </section>
        </div>
      )}

      {newRunOpen && (
        <NewRunModal
          baseUrl={baseUrl}
          apiKey={apiKey}
          model={model}
          headersText={headersText}
          maxContext={maxContext}
          judgeTool={judgeTool}
          judgeModel={judgeModel}
          submitting={submitting}
          onClose={() => setNewRunOpen(false)}
          onSubmit={submit}
          onBaseUrlChange={setBaseUrl}
          onApiKeyChange={setApiKey}
          onModelChange={setModel}
          onHeadersTextChange={setHeadersText}
          onMaxContextChange={setMaxContext}
          onJudgeToolChange={setJudgeTool}
          onJudgeModelChange={setJudgeModel}
        />
      )}
    </div>
  );
}

function NewRunModal({
  baseUrl,
  apiKey,
  model,
  headersText,
  maxContext,
  judgeTool,
  judgeModel,
  submitting,
  onClose,
  onSubmit,
  onBaseUrlChange,
  onApiKeyChange,
  onModelChange,
  onHeadersTextChange,
  onMaxContextChange,
  onJudgeToolChange,
  onJudgeModelChange,
}: {
  baseUrl: string;
  apiKey: string;
  model: string;
  headersText: string;
  maxContext: string;
  judgeTool: "codex" | "pi";
  judgeModel: string;
  submitting: boolean;
  onClose: () => void;
  onSubmit: (e: FormEvent) => void;
  onBaseUrlChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onHeadersTextChange: (value: string) => void;
  onMaxContextChange: (value: string) => void;
  onJudgeToolChange: (value: "codex" | "pi") => void;
  onJudgeModelChange: (value: string) => void;
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !submitting) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, submitting]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink-900/80 px-4 py-8 backdrop-blur-sm">
      <button
        className="absolute inset-0 h-full w-full cursor-default"
        aria-label="Close new run dialog"
        onClick={() => {
          if (!submitting) onClose();
        }}
      />
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-run-title"
        className="panel relative w-full max-w-2xl overflow-hidden"
      >
        <div className="panel-header">
          <div>
            <h2 id="new-run-title" className="text-sm font-semibold text-ink-100">New benchmark run</h2>
            <p className="mt-1 text-xs text-ink-400">Configure the endpoint, model, and judge for the next run.</p>
          </div>
          <button className="btn" onClick={onClose} disabled={submitting}>
            Close
          </button>
        </div>
        <form className="panel-body space-y-4" onSubmit={onSubmit}>
          <label className="block space-y-1">
            <span className="label">Base API URL</span>
            <input
              className="input"
              value={baseUrl}
              onChange={(e) => onBaseUrlChange(e.target.value)}
              placeholder="http://localhost:11434/v1"
              required
            />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block space-y-1">
              <span className="label">API key</span>
              <input
                className="input"
                type="password"
                value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                placeholder="Optional"
              />
            </label>
            <label className="block space-y-1">
              <span className="label">Model override</span>
              <input
                className="input"
                value={model}
                onChange={(e) => onModelChange(e.target.value)}
                placeholder="First /models entry"
              />
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_8rem_minmax(0,1fr)]">
            <label className="block space-y-1">
              <span className="label">Max context tokens</span>
              <input
                className="input"
                type="number"
                min={1024}
                max={262144}
                value={maxContext}
                onChange={(e) => onMaxContextChange(e.target.value)}
              />
            </label>
            <label className="block space-y-1">
              <span className="label">Judge</span>
              <select
                className="input"
                value={judgeTool}
                onChange={(e) => onJudgeToolChange(e.target.value as "codex" | "pi")}
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
                onChange={(e) => onJudgeModelChange(e.target.value)}
                placeholder="gpt-5.5"
              />
            </label>
          </div>
          <label className="block space-y-1">
            <span className="label">Custom headers</span>
            <textarea
              className="input min-h-24 font-mono"
              value={headersText}
              onChange={(e) => onHeadersTextChange(e.target.value)}
              placeholder={"X-Header: value\nX-Another: value"}
            />
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button className="btn-primary" disabled={submitting}>
              {submitting ? "Starting..." : "Run benchmark"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function TestRow({ test, raw }: { test: Record<string, any>; raw: Array<Record<string, any>> }) {
  const artifact = testArtifact(test, raw);
  return (
    <details className="group px-5 py-3 text-sm">
      <summary className="grid cursor-pointer list-none gap-3 md:grid-cols-[1fr_5rem]">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`status-dot ${test.ok ? "completed" : "failed"}`} />
            <span className="min-w-0 truncate font-mono text-ink-100">{String(test.name || "unnamed_test")}</span>
          </div>
          <p className="mt-1 break-words text-xs text-ink-400">{String(test.detail || "")}</p>
        </div>
        <div className="flex items-center justify-between gap-3 md:justify-end">
          <span className="text-xs text-ink-500 group-open:text-accent-400">details</span>
          <span className="font-mono text-ink-200">{Number(test.score || 0).toFixed(2)}</span>
        </div>
      </summary>
      <div className="mt-4 grid gap-3 xl:grid-cols-3">
        <TestArtifact title="Prompt" value={artifact.prompt} />
        <TestArtifact title="Result" value={artifact.result} />
        <TestArtifact title="Expectation" value={artifact.expectation} />
      </div>
    </details>
  );
}

function TestArtifact({ title, value }: { title: string; value: string | null }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900/50 p-3">
      <div className="label">{title}</div>
      {value ? (
        <pre className="event-json mt-2 max-h-72 overflow-auto">{value}</pre>
      ) : (
        <p className="mt-2 text-xs text-ink-500">Not stored for this check.</p>
      )}
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

function testArtifact(test: Record<string, any>, raw: Array<Record<string, any>>) {
  const rawMatches = rawEntriesForTest(test, raw);
  const rawMatch = rawMatches[0];
  const source = {
    ...(rawMatch || {}),
    ...test,
    extra: {
      ...((rawMatch?.extra || {}) as Record<string, any>),
      ...((test.extra || {}) as Record<string, any>),
    },
    metadata: {
      ...((rawMatch?.metadata || {}) as Record<string, any>),
      ...((test.metadata || {}) as Record<string, any>),
    },
  };

  return {
    prompt: pickString(source, [
      "prompt",
      "actual_prompt",
      "input_prompt",
      "request.prompt",
      "request.input",
      "request.messages",
      "messages",
      "extra.prompt",
      "extra.actual_prompt",
      "extra.request.prompt",
      "extra.request.messages",
      "metadata.prompt",
    ]) || promptFromRaw(rawMatches),
    result: pickString(source, [
      "result",
      "actual",
      "output",
      "response",
      "response_text",
      "completion",
      "content",
      "raw_response",
      "extra.result",
      "extra.actual",
      "extra.output",
      "extra.response",
      "extra.response_text",
      "metadata.result",
    ]) || resultFromRaw(rawMatches),
    expectation: pickString(source, [
      "expectation",
      "expected",
      "expected_result",
      "assertion",
      "assertions",
      "criteria",
      "rubric",
      "extra.expectation",
      "extra.expected",
      "extra.expected_result",
      "extra.assertion",
      "extra.assertions",
      "extra.criteria",
      "extra.rubric",
      "metadata.expectation",
    ]) || expectationForTest(test),
  };
}

function rawEntriesForTest(test: Record<string, any>, raw: Array<Record<string, any>>) {
  const testName = String(test.name || "");
  if (!testName) return [];
  return raw.filter((entry) => {
    const kind = String(entry.kind || "");
    const names = [
      kind,
      entry.name,
      entry.test,
      entry.test_name,
      entry.check,
      entry.check_name,
      entry.id,
    ].filter(Boolean).map(String);
    if (names.some((name) => name === testName || name.startsWith(`${testName}_`))) {
      return true;
    }
    if (testName === "tool_multistep" && kind.startsWith("tool_multistep_")) {
      return true;
    }
    if (testName === "coding_flappy_game") {
      return kind === "coding_generation" || kind === "coding_judge_external";
    }
    return false;
  });
}

function promptFromRaw(entries: Array<Record<string, any>>) {
  const request = entries.find((entry) => Array.isArray(entry.request?.messages))?.request;
  if (!request) return null;
  return renderMessages(request.messages);
}

function resultFromRaw(entries: Array<Record<string, any>>) {
  const parts = entries
    .map((entry, index) => {
      const label = entries.length > 1 ? `${entry.kind || `step ${index + 1}`}\n` : "";
      const response = entry.response;
      const choice = Array.isArray(response?.choices) ? response.choices[0] : null;
      const message = choice?.message;
      if (message) {
        return label + renderAssistantMessage(message, choice?.finish_reason);
      }
      const text = response?.text || response?.stdout || response?.content;
      if (text) return label + renderValue(text);
      return label + renderValue(response);
    })
    .filter(Boolean);
  return parts.length ? parts.join("\n\n---\n\n") : null;
}

function renderMessages(messages: any[]) {
  const rendered = messages
    .map((message) => {
      const role = String(message?.role || "message").toUpperCase();
      const content = renderValue(message?.content);
      const calls = renderToolCalls(message?.tool_calls);
      return [role, content, calls].filter(Boolean).join("\n");
    })
    .filter(Boolean);
  return rendered.length ? rendered.join("\n\n") : null;
}

function renderAssistantMessage(message: Record<string, any>, finishReason?: string) {
  const parts = [
    renderValue(message.content),
    renderToolCalls(message.tool_calls),
    finishReason ? `finish_reason: ${finishReason}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join("\n\n") : null;
}

function renderToolCalls(calls: any) {
  if (!Array.isArray(calls) || calls.length === 0) return null;
  return calls
    .map((call, index) => {
      const fn = call?.function || {};
      const name = fn.name || `tool_${index + 1}`;
      const args = fn.arguments || {};
      return `tool_call: ${name}\n${typeof args === "string" ? args : JSON.stringify(args, null, 2)}`;
    })
    .join("\n\n");
}

function expectationForTest(test: Record<string, any>) {
  const name = String(test.name || "");
  const exact: Record<string, string> = {
    tool_schema: "Call record_measurement with sample_id alpha-7, value 42.5, unit ms, and passed true.",
    tool_multistep: "Use add and multiply tools to compute (17 + 25) * 3, then submit 126.",
    json_only: "Return only valid JSON: verdict='pass', numbers=[3, 5, 8], checksum=16.",
    coding_flappy_game: "Return a runnable single-file Flappy Bird style HTML game with controls, collision detection, scoring, and restart.",
  };
  if (exact[name]) return exact[name];
  if (name.startsWith("context_")) {
    return "Return only the hidden KJAS-NEEDLE token from the haystack, with no extra text.";
  }
  if (name.startsWith("agency.")) {
    return `Satisfy the agency scenario checker. Current check detail: ${String(test.detail || "not available")}`;
  }
  return test.detail ? `Pass condition detail: ${String(test.detail)}` : null;
}

function pickString(source: Record<string, any>, paths: string[]) {
  for (const path of paths) {
    const value = getPath(source, path);
    const rendered = renderValue(value);
    if (rendered) return rendered;
  }
  return null;
}

function getPath(source: Record<string, any>, path: string) {
  return path.split(".").reduce<any>((value, part) => {
    if (value == null || typeof value !== "object") return undefined;
    return value[part];
  }, source);
}

function renderValue(value: any): string | null {
  if (value == null || value === "") return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function scoreBadge(run: BenchmarkSummary | BenchmarkDetail) {
  if (run.status === "running") {
    return "badge border-amber-500/30 text-amber-300";
  }
  if (run.status === "failed" || run.status === "cancelled") {
    return "badge border-rose-500/30 text-rose-300";
  }
  return run.usable
    ? "badge border-emerald-500/30 text-emerald-300"
    : "badge border-amber-500/30 text-amber-300";
}
