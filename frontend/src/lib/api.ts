// Tiny API client. Uses cookies for auth and never throws on
// non-2xx: the caller inspects the response and decides what to do.

export interface ApiError {
  status: number;
  detail: string;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers || {}),
    },
    credentials: "same-origin",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") {
        detail = data.detail;
      }
    } catch {
      // ignore
    }
    const err: ApiError = { status: res.status, detail };
    throw err;
  }
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return (await res.json()) as T;
}

export const api = {
  // Auth
  authStatus: () => request<{ enabled: boolean; bootstrap_required: boolean }>("/api/auth/status"),
  login: (passphrase: string) =>
    request<{ ok: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ passphrase }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  bootstrap: (passphrase: string) =>
    request<{ ok: boolean }>("/api/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ passphrase }),
    }),

  // Dashboard
  dashboard: () => request<{ runs: any[] }>("/api/dashboard"),

  // Projects
  projects: () => request<any[]>("/api/projects"),
  selectProjectDirectory: () =>
    request<{ path: string | null }>("/api/projects/select-directory", {
      method: "POST",
    }),
  createProject: (name: string, path: string, createKajasDir = true) =>
    request<any>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, path, create_kajas_dir: createKajasDir }),
    }),
  deleteProject: (name: string) =>
    request<{ ok: boolean }>(`/api/projects/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  // Config
  globalConfig: () => request<any>("/api/config/global"),
  putGlobalConfig: (yaml: string) =>
    request<any>("/api/config/global", {
      method: "PUT",
      body: JSON.stringify({ yaml }),
    }),
  projectConfig: (project: string) =>
    request<any>(
      `/api/config/project?project=${encodeURIComponent(project)}`,
    ),
  putProjectConfig: (project: string, yaml: string) =>
    request<any>(
      `/api/config/project?project=${encodeURIComponent(project)}`,
      {
        method: "PUT",
        body: JSON.stringify({ yaml }),
      },
    ),
  mergedConfig: (project?: string) => {
    const q = project ? `?project=${encodeURIComponent(project)}` : "";
    return request<any>(`/api/config/merged${q}`);
  },

  // Runs
  createRun: (input: {
    project: string;
    workflow: string;
    title: string;
    prompt: string;
    overrides?: Record<string, any>;
  }) =>
    request<any>("/api/runs", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  run: (id: string) => request<any>(`/api/runs/${encodeURIComponent(id)}`),
  approvePlan: (id: string, plan?: string) =>
    request<any>(`/api/runs/${encodeURIComponent(id)}/approve-plan`, {
      method: "POST",
      body: JSON.stringify({ plan: plan ?? null }),
    }),
  cancelRun: (id: string) =>
    request<any>(`/api/runs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  rerunFailedPhase: (id: string) =>
    request<any>(`/api/runs/${encodeURIComponent(id)}/rerun-failed-phase`, {
      method: "POST",
    }),
  deleteRun: (id: string) =>
    request<{ ok: boolean }>(`/api/runs/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  // Health
  health: () => request<any>("/api/health"),
  toolSmoke: () =>
    request<any>("/api/health/tool-smoke", { method: "POST" }),

  // Benchmarks
  benchmarks: () => request<any[]>("/api/benchmarks"),
  createBenchmark: (input: {
    base_url: string;
    api_key?: string | null;
    custom_headers?: Record<string, string>;
    model?: string | null;
    max_context_tokens?: number | null;
    coding_judge_tool?: "codex" | "pi";
    coding_judge_model?: string;
  }) =>
    request<any>("/api/benchmarks", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  benchmark: (id: string) =>
    request<any>(`/api/benchmarks/${encodeURIComponent(id)}`),
  deleteBenchmark: (id: string) =>
    request<{ ok: boolean }>(`/api/benchmarks/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
};

/**
 * Subscribe to the live event stream for a run. Returns a cleanup
 * function. The callback receives each parsed event; the stream also
 * keeps the connection open with heartbeat comments.
 */
export function streamRunEvents(
  runId: string,
  onEvent: (ev: any) => void,
  onError?: (err: unknown) => void,
): () => void {
  const controller = new AbortController();
  fetch(`/api/runs/${encodeURIComponent(runId)}/events/stream`, {
    credentials: "same-origin",
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onError?.(new Error(`stream failed: ${res.status}`));
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of frame.split("\n")) {
            if (line.startsWith("data: ")) {
              const payload = line.slice(6).trim();
              if (!payload) continue;
              try {
                onEvent(JSON.parse(payload));
              } catch {
                /* ignore malformed frame */
              }
            }
          }
        }
      }
    })
    .catch((err) => {
      if (controller.signal.aborted) return;
      onError?.(err);
    });
  return () => controller.abort();
}
