"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink, LoaderCircle, Mail, Clock3 } from "lucide-react";
import { api } from "@/lib/api";
import type { EmailDetail } from "@/types";

function formatDate(value?: string | null) {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, {
    dateStyle: "medium", timeStyle: "short",
  });
}

export default function HoverContextPreview({
  children,
  title,
  description,
  meta,
  emailId,
  createdAt,
  emailUrl,
  sourceLabel,
  destinationUrl,
  destinationLabel,
  timeline,
  onSourceOpen,
  onOpenChange,
}: {
  children: ReactNode;
  title: string;
  description?: string | null;
  meta?: string[];
  emailId?: string | null;
  createdAt?: string | null;
  emailUrl?: string;
  sourceLabel?: string;
  destinationUrl?: string;
  destinationLabel?: string;
  timeline?: { label: string; value: string }[];
  onSourceOpen?: () => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<EmailDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [position, setPosition] = useState<{ top: number; left: number; width: number; maxHeight: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setPreviewOpen = (value: boolean) => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    setOpen(value);
    onOpenChange?.(value);
  };

  const scheduleClose = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => setPreviewOpen(false), 220);
  };

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(480, Math.max(240, window.innerWidth - 24));
      const preferredHeight = Math.min(448, Math.floor(window.innerHeight * 0.7));
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
      const below = Math.max(0, window.innerHeight - rect.bottom - 8);
      const above = Math.max(0, rect.top - 8);
      const placeBelow = below >= Math.min(260, preferredHeight) || below >= above;
      const maxHeight = Math.max(140, Math.min(preferredHeight, placeBelow ? below : above));
      const top = placeBelow ? rect.bottom : Math.max(8, rect.top - maxHeight);
      setPosition({ top, left, width, maxHeight });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open || !emailId || detail?.id === emailId) return;
    let active = true;
    void Promise.resolve()
      .then(() => {
        if (!active) return undefined;
        setLoading(true);
        setLoadError("");
        return api<EmailDetail>(`/emails/${encodeURIComponent(emailId)}`);
      })
      .then((result) => { if (active && result) setDetail(result); })
      .catch((error) => { if (active) setLoadError((error as Error).message || "Email context could not be loaded."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, emailId, detail?.id]);

  return (
    <div
      ref={rootRef}
      className="group/preview relative min-w-0"
      tabIndex={0}
      onClickCapture={(event) => {
        if ((event.target as HTMLElement).closest("button, a, input, select, textarea")) return;
        setPreviewOpen(true);
      }}
      onMouseEnter={() => setPreviewOpen(true)}
      onMouseLeave={scheduleClose}
      onFocusCapture={() => setPreviewOpen(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setPreviewOpen(false);
      }}
    >
      <div>{children}</div>
      {open && position && (
        <section onMouseEnter={() => setPreviewOpen(true)} onMouseLeave={scheduleClose} role="region" aria-label={`Details for ${title}`} style={{ position: "fixed", top: position.top, left: position.left, width: position.width, maxHeight: position.maxHeight }} className="z-[100] rounded-xl border border-border bg-surface p-4 text-left shadow-2xl ring-1 ring-black/5">
          <div className="max-h-[min(28rem,70vh)] overflow-y-auto pr-1">
            <h4 className="text-sm font-semibold leading-5 text-text">{title}</h4>
            {meta?.filter(Boolean).length ? <p className="mt-1 text-[11px] text-text-faint">{meta.filter(Boolean).join(" · ")}</p> : null}
            {description ? <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-text-muted">{description}</p> : <p className="mt-3 text-xs text-text-faint">No additional task details were saved.</p>}
            {createdAt && <p className="mt-3 flex items-center gap-1.5 text-[11px] text-text-faint"><Clock3 size={13} /> Added {formatDate(createdAt)}</p>}
            {timeline?.length ? <ol className="mt-3 space-y-2 border-l border-border-strong pl-3">{timeline.map((point) => <li key={`${point.label}-${point.value}`} className="relative text-[11px]"><span className="absolute -left-[17px] top-1.5 h-1.5 w-1.5 rounded-full bg-primary" /><p className="font-medium text-text">{point.label}</p><p className="text-text-muted">{point.value}</p></li>)}</ol> : null}

            {emailId && <div className="mt-4 border-t border-border pt-3">
              <div className="flex items-center gap-2 text-xs font-semibold text-text"><Mail size={14} className="text-primary" /> Original email &amp; thread</div>
              {loading && <p className="mt-3 flex items-center gap-2 text-xs text-text-muted"><LoaderCircle size={14} className="animate-spin" /> Loading email context…</p>}
              {loadError && <p className="mt-3 text-xs text-high">{loadError}</p>}
              {detail && <>
                <p className="mt-2 text-xs font-medium text-text">{detail.subject}</p>
                <p className="mt-1 text-[11px] text-text-muted">{detail.sender} · Received {formatDate(detail.receivedAt)}</p>
                <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-5 text-text-muted">{detail.body || detail.snippet || "No message text is available."}</p>
                <div className="mt-3 rounded-lg bg-surface-muted/60 p-3">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-text-faint">
                    {detail.timeline.length > 1 ? `${detail.timeline.length} messages in this thread` : "Single message in synced history"}
                  </p>
                  {detail.timeline.length > 0 && <p className="mt-1 text-[10px] text-text-faint">First: {formatDate(detail.timeline[0].receivedAt)} · Latest: {formatDate(detail.timeline[detail.timeline.length - 1].receivedAt)}</p>}
                  {detail.timeline.length ? <ol className="mt-2 space-y-2 border-l border-border-strong pl-3">
                    {detail.timeline.map((item) => <li key={item.id} className="relative text-[11px]">
                      <span className="absolute -left-[calc(0.75rem+3px)] top-1.5 h-1.5 w-1.5 rounded-full bg-primary" />
                      <p className="font-medium text-text">{item.sender}</p>
                      <p className="text-text-faint">{formatDate(item.receivedAt)}</p>
                      {item.snippet && <p className="mt-0.5 line-clamp-2 text-text-muted">{item.snippet}</p>}
                    </li>)}
                  </ol> : <p className="mt-2 text-[11px] text-text-muted">No other synced messages are available for this thread.</p>}
                  <p className="mt-2 text-[10px] text-text-faint">Shows incoming messages synced by Nexus.</p>
                </div>
                <a href={detail.gmailUrl || emailUrl} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary underline">Open in Gmail <ExternalLink size={12} /></a>
              </>}
              {!loading && !detail && !loadError && emailUrl && <a href={emailUrl} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary underline">Open original email <ExternalLink size={12} /></a>}
            </div>}
            {!emailId && emailUrl && <a href={emailUrl} target="_blank" rel="noreferrer" onClick={() => onSourceOpen?.()} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary underline">{sourceLabel || "Open original source"} <ExternalLink size={12} /></a>}
            {destinationUrl && <a href={destinationUrl} className="ml-3 mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary underline">{destinationLabel || "Open details"} <ExternalLink size={12} /></a>}
          </div>
        </section>
      )}
    </div>
  );
}
