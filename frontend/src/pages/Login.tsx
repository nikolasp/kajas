import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";

export function Login() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "bootstrap" | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .authStatus()
      .then((s) => {
        setMode(s.bootstrap_required ? "bootstrap" : "login");
      })
      .catch(() => setMode("login"));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "bootstrap") {
        if (passphrase.length < 4) {
          setError("Passphrase must be at least 4 characters.");
          return;
        }
        if (passphrase !== confirm) {
          setError("Passphrases do not match.");
          return;
        }
        await api.bootstrap(passphrase);
      }
      await api.login(passphrase);
      navigate("/");
    } catch (err: any) {
      setError(err?.detail || "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-ink-900">
      <form
        onSubmit={submit}
        className="panel w-96 max-w-full p-6"
      >
        <h1 className="text-lg font-semibold text-ink-100">
          {mode === "bootstrap" ? "Set admin passphrase" : "Sign in"}
        </h1>
        <p className="mt-1 text-sm text-ink-400">
          {mode === "bootstrap"
            ? "Kajas is not configured yet. Pick a passphrase to lock the local UI."
            : "Enter the local admin passphrase to continue."}
        </p>
        <div className="mt-5 space-y-3">
          <label className="block">
            <span className="label">passphrase</span>
            <input
              className="input mt-1"
              type="password"
              autoFocus
              autoComplete="current-password"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
            />
          </label>
          {mode === "bootstrap" && (
            <label className="block">
              <span className="label">confirm</span>
              <input
                className="input mt-1"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </label>
          )}
          {error && (
            <p className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
              {error}
            </p>
          )}
          <button type="submit" className="btn-primary w-full justify-center" disabled={busy}>
            {busy ? "Working…" : mode === "bootstrap" ? "Set passphrase" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}
