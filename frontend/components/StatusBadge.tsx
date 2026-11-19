import { TaskStatus } from "@/types";

const config: Record<TaskStatus, { label: string; className: string }> = {
  pending: { label: "Pending", className: "text-text-muted bg-surface-muted" },
  "in-progress": { label: "In Progress", className: "text-primary bg-primary-dim" },
  completed: { label: "Completed", className: "text-low bg-low-bg" },
};

export default function StatusBadge({ status }: { status: TaskStatus }) {
  const c = config[status];
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${c.className}`}
    >
      {c.label}
    </span>
  );
}
