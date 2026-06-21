// Shared TypeScript types mirroring the backend Pydantic models.

export type RunStatus =
  | "draft"
  | "planning"
  | "awaiting_plan_approval"
  | "implementing"
  | "verifying"
  | "awaiting_final_acceptance"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "deleted";

export interface UsageBlock {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
}

export interface RunSummary {
  id: string;
  project: string;
  project_path: string;
  title: string;
  status: RunStatus;
  workflow: string;
  started_at: string;
  updated_at: string;
  usage: Record<string, UsageBlock | null>;
  total_tokens: number | null;
  planner_agent: string | null;
  implementor_agent: string | null;
  plan_approved_at: string | null;
  error: string | null;
}

export interface RunDetail extends RunSummary {
  prompt: string;
  effective_config: any;
  final_summary: string | null;
  plan: string | null;
  approved_plan: string | null;
}

export interface ProjectInfo {
  name: string;
  path: string;
  has_kajas_dir: boolean;
  is_git: boolean;
  config: Record<string, any>;
}

export interface NormalizedEvent {
  type:
    | "message"
    | "tool_call"
    | "tool_result"
    | "approval_request"
    | "usage"
    | "artifact"
    | "final"
    | "error"
    | "log"
    | "status";
  stage: "planning" | "implementation";
  status?: RunStatus | null;
  text?: string | null;
  name?: string | null;
  summary?: string | null;
  args?: Record<string, any> | null;
  result?: string | null;
  reason?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  artifact?: string | null;
  message?: string | null;
  ts: string;
  extra?: Record<string, any>;
}

export interface CheckResult {
  name: string;
  ok: boolean;
  detail: string;
  extra?: Record<string, any>;
}

export interface HealthReport {
  ok: boolean;
  checks: CheckResult[];
}

export interface AuthStatus {
  enabled: boolean;
  bootstrap_required: boolean;
}
