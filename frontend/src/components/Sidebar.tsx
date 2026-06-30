import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  PlusCircle,
  BarChart2,
  FolderKanban,
  Settings,
  Activity,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Sun,
  Moon,
} from "lucide-react";

type Theme = "dark" | "light";

type NavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
};

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/runs/new", label: "New Run", icon: PlusCircle },
  { to: "/benchmark", label: "Benchmark", icon: BarChart2 },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/config", label: "Config", icon: Settings },
  { to: "/health", label: "Health", icon: Activity },
];

export default function Sidebar({
  projects = [],
  authEnabled,
  onLogout,
}: {
  projects?: any[];
  authEnabled?: boolean;
  onLogout?: () => void;
}) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem("kajas-sidebar-collapsed") === "true";
    } catch {
      return false;
    }
  });

  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("kajas-theme", theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem("kajas-sidebar-collapsed", collapsed ? "true" : "false");
  }, [collapsed]);

  function toggleTheme() {
    setTheme((t) => (t === "light" ? "dark" : "light"));
  }

  return (
    <aside className={`relative flex h-full shrink-0 flex-col border-r border-ink-800 bg-ink-950/70 transition-[width] duration-200 ease-in-out ${collapsed ? "w-16" : "w-60"}`}>
      {/* Logo */}
      <div className={`border-b border-ink-800 py-5 transition-[padding] duration-200 ease-in-out ${collapsed ? "px-1" : "px-5"}`}>
        <Link
          to="/"
          className="flex items-center gap-2 no-underline hover:text-ink-100"
          aria-label="Kajas dashboard"
          title={collapsed ? "Dashboard" : undefined}
        >
          <img
            src="/logo.svg"
            alt=""
            className="h-9 w-12 shrink-0 object-contain object-left"
          />
          {!collapsed && (
            <div className="min-w-0">
              <span className="relative text-base font-semibold">
                Kajas
                <span className="absolute -bottom-1 left-0 h-0.5 w-full rounded-full bg-[#c6a15f]" />
              </span>
              <p className="text-xs text-ink-400">agentic coding harness</p>
            </div>
          )}
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 py-3 text-sm transition-[padding] duration-200 ease-in-out" style={{ paddingInline: collapsed ? "0.25rem" : "0.75rem" }}>
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                [
                  `flex items-center ${collapsed ? 'justify-center' : 'justify-start gap-3'}`,
                  "rounded-lg px-3 py-2.5 transition",
                  isActive
                    ? "bg-accent-600 text-white shadow-sm shadow-accent-600/20 hover:text-white"
                    : "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
                ].join(" ")
              }
              title={collapsed ? item.label : undefined}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </NavLink>
          );
        })}
      </nav>

      {/* Bottom bar: collapse toggle + theme + logout */}
      <div className="border-t border-ink-800 text-xs text-ink-400" style={{ padding: collapsed ? "0.75rem 0.25rem" : "0.75rem 0.75rem" }}>
        <div className={`flex flex-col gap-1 ${collapsed ? "items-center" : ""}`}>
          <button
            type="button"
            className={`flex items-center ${collapsed ? 'justify-center' : 'justify-start gap-3'} w-full rounded-md hover:bg-ink-800 px-2 py-1`}
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <ChevronRight className="h-4 w-4 shrink-0" /> : <ChevronLeft className="h-4 w-4 shrink-0" />}
            {!collapsed && <span className="truncate">collapse</span>}
          </button>
          <button
            type="button"
            className={`flex items-center ${collapsed ? 'justify-center' : 'justify-start gap-3'} w-full rounded-md hover:bg-ink-800 px-2 py-1`}
            aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
            title={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
            onClick={toggleTheme}
          >
            {theme === "light" ? <Moon className="h-4 w-4 shrink-0" /> : <Sun className="h-4 w-4 shrink-0" />}
            {!collapsed && <span className="truncate">{theme === "light" ? "dark mode" : "light mode"}</span>}
          </button>
          {authEnabled && onLogout && (
            <button
              onClick={onLogout}
              className={`flex items-center ${collapsed ? 'justify-center' : 'justify-start gap-3'} w-full rounded-md ${collapsed ? '' : 'text-left'} hover:bg-ink-800 px-2 py-1`}
              title={collapsed ? "Log out" : undefined}
            >
              <LogOut className="h-4 w-4 shrink-0" />
              {!collapsed && <span className="truncate">log out</span>}
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
