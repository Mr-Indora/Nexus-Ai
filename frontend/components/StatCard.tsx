import { LucideIcon, TrendingUp, TrendingDown } from "lucide-react";

interface StatCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  trend?: { value: string; positive: boolean };
  accent?: string;
}

export default function StatCard({ label, value, icon: Icon, trend, accent = "#2A56E8" }: StatCardProps) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm sm:p-5">
      <div className="flex items-start justify-between">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-lg"
          style={{ backgroundColor: `${accent}15`, color: accent }}
        >
          <Icon size={18} strokeWidth={2} />
        </div>
        {trend && (
          <div
            className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${
              trend.positive ? "bg-low-bg text-low" : "bg-high-bg text-high"
            }`}
          >
            {trend.positive ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
            {trend.value}
          </div>
        )}
      </div>
      <div className="mt-3 font-display text-2xl font-semibold text-text sm:text-[28px]">{value}</div>
      <div className="mt-0.5 text-[13px] text-text-muted">{label}</div>
    </div>
  );
}
