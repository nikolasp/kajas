import type { RunStatus } from "../lib/types";

const LABELS: Record<RunStatus, string> = {
  draft: "draft",
  planning: "planning",
  awaiting_plan_approval: "awaiting approval",
  implementing: "implementing",
  verifying: "verifying",
  awaiting_final_acceptance: "awaiting acceptance",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
  deleted: "deleted",
};

export function StatusPill({ status }: { status: RunStatus | string }) {
  const s = (status || "draft") as RunStatus;
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 whitespace-nowrap">
      <span className={`status-dot ${s} shrink-0`} />
      <span className="text-xs text-ink-200">{LABELS[s] ?? s}</span>
    </span>
  );
}
