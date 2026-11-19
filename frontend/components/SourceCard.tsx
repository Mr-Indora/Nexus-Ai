import { Source } from "@/types";
import { sourceMeta } from "@/lib/display";
import { timeAgo } from "@/lib/format";
import { AlertCircle, CheckCircle2 } from "lucide-react";

export default function SourceCard({ source }: { source: Source }) {
  const meta = sourceMeta[source.type];
  const Icon = meta.icon;
  const isConnected = source.status === "connected" || source.status === "active";

  return (
    <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div
          className="flex h-11 w-11 items-center justify-center rounded-xl"
          style={{ backgroundColor: `${meta.color}15`, color: meta.color }}
        >
          <Icon size={21} strokeWidth={2} />
        </div>
        <span
          className={`flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold ${
          isConnected
              ? "bg-low-bg text-low"
              : "bg-surface-muted text-text-faint"
          }`}
        >
          {isConnected ? <CheckCircle2 size={12} /> : <AlertCircle size={12} />}
          {source.status === "connected" ? "Connected" : source.status === "active" ? "Active" : "Disconnected"}
        </span>
      </div>

      <h3 className="mt-4 text-[15px] font-semibold text-text">{source.name}</h3>
      <p className="mt-1 text-[12.5px] leading-relaxed text-text-muted">{source.description}</p>

      <div className="mt-4 flex items-center justify-between text-[11.5px] text-text-faint">
        <span>{source.itemCount} items ingested</span>
        <span>{source.lastSynced ? `Last successful sync ${timeAgo(source.lastSynced)}` : "No successful sync yet"}</span>
      </div>
      {source.type === "calendar" && <a href="/api/auth/google/login" className="mt-3 inline-flex text-[11px] font-semibold text-primary underline underline-offset-2">Refresh Google permissions to enable event edits</a>}
    </div>
  );
}
