import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "./lib/api";

type Theme = "dark" | "light";

const NAV = [
  { to: "/", label: "Dashboard" },
  { to: "/runs/new", label: "New Run" },
  { to: "/benchmark", label: "Benchmark" },
  { to: "/projects", label: "Projects" },
  { to: "/config", label: "Config" },
  { to: "/health", label: "Health" },
];

export default function App() {
  const navigate = useNavigate();
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [authed, setAuthed] = useState<boolean>(false);
  const [projects, setProjects] = useState<any[]>([]);
  const [theme, setTheme] = useState<Theme>(() => initialTheme());

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("kajas-theme", theme);
  }, [theme]);

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
      <aside className="flex w-60 flex-col border-r border-ink-800 bg-ink-950/70">
        <div className="border-b border-ink-800 px-5 py-5">
          <Link
            to="/"
            className="flex items-center gap-2 text-ink-100 no-underline hover:text-ink-100"
            aria-label="Kajas dashboard"
          >
            <img
              src="/logo.svg"
              alt=""
              className="h-9 w-12 shrink-0 object-contain object-left"
            />
            <span className="relative text-base font-semibold">
              Kajas
              <span className="absolute -bottom-1 left-0 h-0.5 w-full rounded-full bg-[#c6a15f]" />
            </span>
          </Link>
          <p className="mt-0.5 text-xs text-ink-400">
            agentic coding harness
          </p>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-4 text-sm">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                [
                  "block rounded-lg px-3 py-2.5 transition",
                  isActive
                    ? "bg-accent-600 text-white shadow-sm shadow-accent-600/20 hover:text-white"
                    : "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
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
              className="mb-2 block w-full rounded-md px-2 py-1 text-left hover:bg-ink-800"
            >
              log out
            </button>
          )}
          <div className="flex items-center justify-between gap-3">
            <div className="truncate">
              {projects.length} project{projects.length === 1 ? "" : "s"}
            </div>
            <button
              type="button"
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-ink-700 bg-ink-800 text-ink-200 transition hover:bg-ink-700 hover:text-ink-100"
              aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
              title={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
              onClick={() => setTheme(theme === "light" ? "dark" : "light")}
            >
              {theme === "light" ? <MoonIcon /> : <SunIcon />}
            </button>
          </div>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}

function initialTheme(): Theme {
  const stored = window.localStorage.getItem("kajas-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function SunIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.75v2.5M12 18.75v2.5M4.43 4.43l1.77 1.77M17.8 17.8l1.77 1.77M2.75 12h2.5M18.75 12h2.5M4.43 19.57l1.77-1.77M17.8 6.2l1.77-1.77" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.25 14.16A7.7 7.7 0 0 1 9.84 3.75a8.25 8.25 0 1 0 10.41 10.41Z" />
    </svg>
  );
}
