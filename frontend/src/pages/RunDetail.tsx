import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, streamRunEvents } from "../lib/api";
import { StatusPill } from "../components/StatusPill";
import { fmtNumber, timeAgo } from "../lib/format";

export function RunDetail() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<any | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [editPlan, setEditPlan] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCancelOpen, setConfirmCancelOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [streamKey, setStreamKey] = useState(0);
  const eventsRef = useRef<HTMLDivElement | null>(null);

  // Pull initial run record and the merged config in parallel.
  useEffect(() => {
    setRun(null);
    setEvents([]);
    api
      .run(runId)
      .then((r) => {
        setRun(r);
        if (r.plan_approved_at === null && r.status === "awaiting_plan_approval") {
          setEditPlan(null);
        }
      })
      .catch((e) => setError(e.detail));
  }, [runId, streamKey]);

  // Subscribe to the live event stream.
  useEffect(() => {
    if (!runId) return;
    const stop = streamRunEvents(
      runId,
      (ev) => {
        setEvents((prev) => {
          // Dedup by ts+type+summary, since the backend may replay the
          // historical events.ndjson before going live.
          const last = prev[prev.length - 1];
          if (
            last &&
            last.ts === ev.ts &&
            last.type === ev.type &&
            (last.summary || last.text || "") === (ev.summary || ev.text || "")
          ) {
            return prev;
          }
          return [...prev, ev];
        });
        // Status events are emitted after run.md has been persisted,
        // so this fetch observes the committed run state.
        if (ev.type === "status" || ev.type === "final" || ev.type === "error") {
          api.run(runId).then(setRun).catch(() => {});
        }
      },
      (err) => {
        // Stream errors are non-fatal; surface as a banner.
        setError(String((err as Error)?.message || err));
      },
    );
    return stop;
  }, [runId]);

  // Auto-scroll events panel.
  useEffect(() => {
    const el = eventsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  const totalTokens = useMemo(() => {
    if (!run) return null;
    let total = 0;
    for (const v of Object.values(run.usage || {})) {
      const t = (v as any)?.total_tokens;
      if (typeof t === "number") total += t;
    }
    return total || null;
  }, [run]);

  async function approve(plan?: string) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.approvePlan(runId, plan);
      setRun(updated);
      setEditPlan(null);
    } catch (err: any) {
      setError(err.detail || "Approve failed");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.cancelRun(runId);
      setRun(updated);
      setConfirmCancelOpen(false);
    } catch (err: any) {
      setError(err.detail || "Cancel failed");
    } finally {
      setBusy(false);
    }
  }

  async function rerunFailedPhase() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.rerunFailedPhase(runId);
      setRun(updated);
      setStreamKey((n) => n + 1);
    } catch (err: any) {
      setError(err.detail || "Rerun failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteRun(runId);
      navigate("/");
    } catch (err: any) {
      setError(err.detail || "Delete failed");
      setConfirmDeleteOpen(false);
    } finally {
      setBusy(false);
    }
  }

  if (error && !run) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      </div>
    );
  }
  if (!run) {
    return <div className="p-8 text-ink-400">Loading…</div>;
  }

  const inFlight = [
    "planning",
    "implementing",
    "verifying",
    "awaiting_plan_approval",
    "awaiting_final_acceptance",
  ].includes(run.status);
  const canDelete = ["completed", "failed", "cancelled", "interrupted"].includes(
    run.status,
  );

  return (
    <>
      <div className="mx-auto grid max-w-7xl gap-6 p-8 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold">
                {run.title || run.id}
              </h1>
              <StatusPill status={run.status} />
            </div>
            <p className="mt-1 text-xs text-ink-400">
              <code>{run.id}</code> · {run.project} · {run.workflow} ·{" "}
              updated {timeAgo(run.updated_at)}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {inFlight && (
              <button
                className="btn-danger"
                onClick={() => setConfirmCancelOpen(true)}
                disabled={busy}
              >
                Cancel
              </button>
            )}
            {run.status === "failed" && (
              <button className="btn-primary" onClick={rerunFailedPhase} disabled={busy}>
                Rerun failed phase
              </button>
            )}
            {canDelete && (
              <button
                className="btn-danger"
                onClick={() => setConfirmDeleteOpen(true)}
                disabled={busy}
              >
                Delete
              </button>
            )}
          </div>
        </header>

        {error && (
          <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
            {error}
          </div>
        )}

        {run.status === "awaiting_plan_approval" && (
          <PlanApproval
            run={run}
            events={events}
            editPlan={editPlan}
            setEditPlan={setEditPlan}
            onApprove={approve}
            busy={busy}
          />
        )}

        <section className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">Timeline</h2>
            <span className="text-xs text-ink-400">
              {events.length} event{events.length === 1 ? "" : "s"}
            </span>
          </div>
          <div
            ref={eventsRef}
            className="h-[55vh] overflow-y-auto p-3 font-mono text-xs"
          >
            {events.length === 0 ? (
              <div className="p-3 text-ink-400">No events yet.</div>
            ) : (
              events.map((ev, i) => <EventRow key={i} ev={ev} />)
            )}
          </div>
        </section>
        </div>

        <aside className="space-y-4">
        <section className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">Usage</h2>
          </div>
          <div className="panel-body space-y-2 text-sm">
            {Object.entries(run.usage || {}).map(([stage, u]: any) => (
              <div key={stage} className="flex items-center justify-between">
                <span className="text-ink-300">{stage}</span>
                <span className="font-mono text-ink-200">
                  {u ? fmtNumber(u.total_tokens) : "—"}
                </span>
              </div>
            ))}
            <div className="mt-2 flex items-center justify-between border-t border-ink-700 pt-2">
              <span className="text-ink-400">total</span>
              <span className="font-mono text-ink-100">
                {fmtNumber(totalTokens)}
              </span>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2 className="text-sm font-semibold text-ink-200">Prompt</h2>
          </div>
          <pre className="panel-body whitespace-pre-wrap text-xs text-ink-200">
            {run.prompt}
          </pre>
        </section>

        {run.error && (
          <section className="panel">
            <div className="panel-header">
              <h2 className="text-sm font-semibold text-rose-300">Error</h2>
            </div>
            <pre className="panel-body whitespace-pre-wrap text-xs text-rose-200">
              {run.error}
            </pre>
          </section>
        )}
        </aside>
      </div>

      {confirmCancelOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/80 p-4">
          <div
            className="w-full max-w-md rounded-lg border border-rose-500/30 bg-ink-900 p-5 shadow-2xl shadow-ink-900"
            role="dialog"
            aria-modal="true"
            aria-labelledby="cancel-run-title"
          >
            <h2 id="cancel-run-title" className="text-lg font-semibold text-ink-100">
              Cancel run
            </h2>
            <p className="mt-2 text-sm text-ink-300">
              Cancel this run? Any active subprocesses will be terminated.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="btn"
                onClick={() => setConfirmCancelOpen(false)}
                disabled={busy}
              >
                Keep running
              </button>
              <button className="btn-danger" onClick={cancel} disabled={busy}>
                Cancel run
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDeleteOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/80 p-4">
          <div
            className="w-full max-w-md rounded-lg border border-rose-500/30 bg-ink-900 p-5 shadow-2xl shadow-ink-900"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-run-title"
          >
            <h2 id="delete-run-title" className="text-lg font-semibold text-ink-100">
              Delete run
            </h2>
            <p className="mt-2 text-sm text-ink-300">
              Delete this run folder? This cannot be undone.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="btn"
                onClick={() => setConfirmDeleteOpen(false)}
                disabled={busy}
              >
                Cancel
              </button>
              <button className="btn-danger" onClick={remove} disabled={busy}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function EventRow({ ev }: { ev: any }) {
  return (
    <div className="border-b border-ink-800/60 px-2 py-1.5 last:border-b-0">
      <div className="flex items-center gap-2 text-[0.65rem] uppercase tracking-wider text-ink-400">
        <span>{ev.stage}</span>
        <span>·</span>
        <span>{ev.type}</span>
        <span className="ml-auto">{new Date(ev.ts).toLocaleTimeString()}</span>
      </div>
      <pre className="event-json mt-0.5">
        {ev.text ||
          ev.status ||
          ev.message ||
          ev.summary ||
          ev.result ||
          (ev.artifact ? `artifact=${ev.artifact}` : "") ||
          (ev.name ? `${ev.name}()` : "")}
      </pre>
    </div>
  );
}

function PlanApproval({
  run,
  events,
  editPlan,
  setEditPlan,
  onApprove,
  busy,
}: {
  run: any;
  events: any[];
  editPlan: string | null;
  setEditPlan: (s: string | null) => void;
  onApprove: (plan?: string) => void;
  busy: boolean;
}) {
  // The most recent message starting with "Drafting" is the
  // planner's draft. Better: surface the final event's plan_yaml.
  const planYaml = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "final" && e.artifact === "plan.md") {
        return e.extra?.plan_yaml || "";
      }
    }
    return run?.plan || "";
  }, [events, run?.plan]);

  return (
    <section className="panel border-accent-500/40">
      <div className="panel-header bg-accent-600/10">
        <h2 className="text-sm font-semibold text-accent-400">
          Awaiting plan approval
        </h2>
        <div className="flex gap-2">
          <button
            className="btn"
            onClick={() => setEditPlan(planYaml)}
            disabled={busy}
          >
            Edit before approving
          </button>
          <button
            className="btn-primary"
            onClick={() => onApprove(editPlan ?? planYaml)}
            disabled={busy}
          >
            Approve &amp; run implementor
          </button>
        </div>
      </div>
      <div className="panel-body">
        {editPlan !== null ? (
          <textarea
            className="input h-64 font-mono text-xs"
            value={editPlan}
            onChange={(e) => setEditPlan(e.target.value)}
          />
        ) : (
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs text-ink-200">
            {planYaml || "(planner has not produced a plan yet)"}
          </pre>
        )}
      </div>
    </section>
  );
}
