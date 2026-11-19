import { Priority } from "@/types";
import { ArrowUp, Minus, ArrowDown } from "lucide-react";

const config: Record<
  Priority,
  { label: string; text: string; bg: string; icon: React.ElementType }
> = {
  high: { label: "High", text: "text-high", bg: "bg-high-bg", icon: ArrowUp },
  medium: { label: "Medium", text: "text-med", bg: "bg-med-bg", icon: Minus },
  low: { label: "Low", text: "text-low", bg: "bg-low-bg", icon: ArrowDown },
};

export default function PriorityBadge({ priority }: { priority: Priority }) {
  const c = config[priority];
  const Icon = c.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ${c.text} ${c.bg}`}
    >
      <Icon size={11} strokeWidth={2.75} />
      {c.label}
    </span>
  );
}
