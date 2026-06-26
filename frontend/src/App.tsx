import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "./lib/api";

const NAV = [
  { to: "/", label: "Dashboard" },
  { to: "/projects", label: "Projects" },
  { to: "/config", label: "Config" },
  { to: "/runs/new", label: "New Run" },
  { to: "/benchmark", label: "Benchmark" },
  { to: "/health", label: "Health" },
];

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
      <aside className="flex w-56 flex-col border-r border-ink-800 bg-ink-900/80">
        <div className="border-b border-ink-800 px-5 py-4">
          <Link
            to="/"
            className="flex items-center gap-3 text-ink-100"
            aria-label="Kajas dashboard"
          >
            <img
              src="/logo.svg"
              alt=""
              className="h-10 w-36 shrink-0 object-contain object-left"
            />
          </Link>
          <p className="mt-0.5 text-xs text-ink-400">
            agentic coding harness
          </p>
        </div>
        <nav className="flex-1 space-y-0.5 px-2 py-3 text-sm">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                [
                  "block rounded-lg px-3 py-2 transition",
                  isActive
                    ? "bg-accent-600/20 text-accent-400"
                    : "text-ink-300 hover:bg-ink-800/70 hover:text-ink-100",
                ].join(" ")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-ink-800 px-3 py-3 text-xs text-ink-400">
          {authEnabled && (
            <button
              onClick={logout}
              className="block w-full rounded-md px-2 py-1 text-left hover:bg-ink-800/70"
            >
              log out
            </button>
          )}
          <div className="mt-2 truncate">
            {projects.length} project{projects.length === 1 ? "" : "s"}
          </div>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
