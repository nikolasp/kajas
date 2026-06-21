import { useEffect, useState } from "react";
import { api } from "../lib/api";

export function Health() {
  const [report, setReport] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [smoking, setSmoking] = useState(false);
  const [smokeAck, setSmokeAck] = useState(false);

  const reload = () =>
    api
      .health()
      .then(setReport)
      .catch((e) => setError(e.detail));

  useEffect(() => {
    reload();
  }, []);

  async function runSmoke() {
    if (!smokeAck) return;
    setSmoking(true);
    setError(null);
    try {
      const r = await api.toolSmoke();
      setReport((prev: any) => ({
        ok: (prev?.checks || []).every((c: any) => c.ok) && r.ok,
        checks: [...(prev?.checks || []), ...(r.checks || [])],
      }));
    } catch (e: any) {
      setError(e.detail || "smoke failed");
    } finally {
      setSmoking(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Health</h1>
          <p className="text-sm text-ink-400">
            Backend self-checks. Tool smoke checks are opt-in and may consume tokens.
          </p>
        </div>
        <button
          onClick={reload}
          className="btn"
          disabled={smoking}
        >
          Refresh
        </button>
      </header>

      {error && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <section className="panel">
        <div className="panel-header">
          <h2 className="text-sm font-semibold text-ink-200">Checks</h2>
          {report && (
            <span
              className={`badge ${
                report.ok
                  ? "border-emerald-500/30 text-emerald-300"
                  : "border-rose-500/30 text-rose-300"
              }`}
            >
              {report.ok ? "all green" : "issues found"}
            </span>
          )}
        </div>
        <div>
          {!report ? (
            <div className="p-5 text-sm text-ink-400">Loading…</div>
          ) : (
            report.checks.map((c: any) => (
              <div
                key={c.name}
                className="flex items-center justify-between border-b border-ink-800 px-5 py-2.5 text-sm last:border-b-0"
              >
                <div>
                  <div className="font-mono text-ink-100">{c.name}</div>
                  <div className="text-xs text-ink-400">{c.detail}</div>
                </div>
                <span
                  className={`badge ${
                    c.ok
                      ? "border-emerald-500/30 text-emerald-300"
                      : "border-rose-500/30 text-rose-300"
                  }`}
                >
                  {c.ok ? "ok" : "fail"}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="text-sm font-semibold text-ink-200">Tool smoke</h2>
        </div>
        <div className="panel-body space-y-3 text-sm text-ink-300">
          <p>
            Runs a tiny probe against each registered tool. With real
            codex/pi adapters, this may send a single short request
            through the model and therefore consume tokens.
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={smokeAck}
              onChange={(e) => setSmokeAck(e.target.checked)}
            />
            <span>I understand this may consume tokens.</span>
          </label>
          <button
            className="btn-primary"
            disabled={!smokeAck || smoking}
            onClick={runSmoke}
          >
            {smoking ? "Running…" : "Run tool smoke"}
          </button>
        </div>
      </section>
    </div>
  );
}
