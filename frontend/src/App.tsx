import { useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { api } from "./lib/api";
import Sidebar from "./components/Sidebar";

export default function App() {
  const navigate = useNavigate();
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [authed, setAuthed] = useState<boolean>(false);
  const [projects, setProjects] = useState<any[]>([]);

  useEffect(() => {
    (async () => {
      try {
        const status = await api.authStatus();
        setAuthEnabled(status.enabled);
        // Try a simple authenticated call to figure out if we have a
        // valid session cookie. If 401, the API client will throw and
        // we redirect to /login.
        await api.dashboard();
        setAuthed(true);
      } catch (err: any) {
        if (err && err.status === 401) {
          navigate("/login");
        } else if (err && err.status === 409) {
          // No projects yet or some other 409 - treat as authed.
          setAuthed(true);
        } else if (err && err.status === 503) {
          navigate("/login");
        }
      }
    })();
  }, [navigate]);

  useEffect(() => {
    if (!authed) return;
    api
      .projects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, [authed]);

  async function logout() {
    await api.logout();
    navigate("/login");
  }

  if (authEnabled === null) {
    return (
      <div className="flex h-full items-center justify-center text-ink-300">
        Loading…
      </div>
    );
  }

  if (authEnabled && !authed) {
    return null;
  }

  return (
    <div className="flex h-full">
      <Sidebar
        projects={projects}
        authEnabled={authEnabled}
        onLogout={logout}
      />
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}


