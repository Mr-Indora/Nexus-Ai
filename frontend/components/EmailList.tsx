"use client";

import { useState } from "react";
import { CheckCircle2, ExternalLink, Mail, X } from "lucide-react";
import { EmailItem } from "@/types";
import { senderName, timeAgo } from "@/lib/format";
import { api } from "@/lib/api";

type EmailDetail = {
  id: string;
  sender: string;
  subject: string;
  body: string;
  receivedAt: string | null;
  gmailUrl: string;
  timelineCoverage: string;
  timeline: { id: string; sender: string; subject: string; receivedAt: string | null; snippet: string }[];
};

export default function EmailList({ emails, limit = 8 }: { emails: EmailItem[]; limit?: number }) {
  const [details, setDetails] = useState<Record<string, EmailDetail>>({});
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [selected, setSelected] = useState<EmailDetail | null>(null);

  const loadDetail = async (id: string) => {
    if (details[id]) return details[id];
    try {
      const value = await api<EmailDetail>(`/emails/${encodeURIComponent(id)}`);
      setDetails((current) => ({ ...current, [id]: value }));
      return value;
    } catch {
      return null;
    }
  };

  const openEmail = async (id: string) => {
    const detail = await loadDetail(id);
    if (detail) {
      setSelected(detail);
    }
  };

  if (emails.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border-strong bg-surface py-10 text-center text-[13px] text-text-muted">
        No emails synced yet. Hit “Sync now”.
      </div>
    );
  }

  return (
    <>
      <div className="divide-y divide-border rounded-xl border border-border bg-surface shadow-sm">
        {emails.slice(0, limit).map((email) => {
          const detail = details[email.id];
          return (
            <div key={email.id} className="group relative px-4 py-3 first:rounded-t-xl last:rounded-b-xl hover:bg-surface-muted/40" onMouseEnter={() => { setPreviewId(email.id); void loadDetail(email.id); }} onMouseLeave={() => setPreviewId(null)}>
              <button type="button" onClick={() => void openEmail(email.id)} onFocus={() => { setPreviewId(email.id); void loadDetail(email.id); }} className="flex w-full items-start gap-3 text-left">
                <span className="relative mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-dim text-primary">
                  <Mail size={15} />
                  <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-primary">
                    <span className="absolute inset-0 rounded-full bg-primary motion-safe:animate-ping" />
                  </span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-[13px] font-semibold text-text">{email.subject}</span>
                    <span className="shrink-0 text-[11px] text-text-faint">{timeAgo(email.receivedAt)}</span>
                  </span>
                  <span className="block truncate text-[12px] text-text-muted">{senderName(email.sender)}</span>
                  <span className="mt-0.5 block truncate text-[12px] text-text-faint">{email.snippet}</span>
                </span>
                {email.actionable && (
                  <span className="mt-0.5 flex shrink-0 items-center gap-1 rounded-md bg-low-bg px-1.5 py-0.5 text-[10.5px] font-semibold text-low">
                    <CheckCircle2 size={11} /> Task
                  </span>
                )}
              </button>
              {previewId === email.id && detail && !selected && (
                <div role="tooltip" className="absolute left-12 right-3 top-[calc(100%-4px)] z-20 rounded-lg border border-border bg-surface p-3 shadow-xl">
                  <p className="line-clamp-3 text-[12px] leading-5 text-text-muted">{detail.body || email.snippet || "No message body was saved."}</p>
                  <span className="mt-2 block text-[10px] text-primary">Click to open timeline and details</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelected(null); }}>
          <section role="dialog" aria-modal="true" aria-labelledby="email-detail-title" className="max-h-[88dvh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-2xl sm:p-7">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h2 id="email-detail-title" className="text-lg font-semibold text-text">{selected.subject || "Email details"}</h2>
                <p className="mt-1 text-sm text-text-muted">{senderName(selected.sender)} · {selected.receivedAt ? new Date(selected.receivedAt).toLocaleString("en-IN") : "time unavailable"}</p>
              </div>
              <button type="button" aria-label="Close email details" onClick={() => setSelected(null)} className="rounded-lg p-2 text-text-muted hover:bg-surface-muted"><X size={18} /></button>
            </div>

            <div className="mt-5 grid gap-5 lg:grid-cols-[1.2fr_.8fr]">
              <div>
                <h3 className="text-sm font-semibold text-text">Message</h3>
                <div className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl border border-border bg-surface-muted/50 p-4 text-[13px] leading-6 text-text">{selected.body || "No message body was saved; open the original email for the full content."}</div>
                <a href={selected.gmailUrl} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline">Open original in Gmail <ExternalLink size={14} /></a>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-text">Conversation timeline</h3>
                <p className="mt-1 text-[11px] text-text-faint">Synced incoming messages only. Sent-mail history may be missing.</p>
                <ol className="mt-3 space-y-3 border-l border-border pl-4">
                  {selected.timeline.map((item) => (
                    <li key={item.id} className="relative">
                      <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-primary" />
                      <p className="text-[11px] text-text-faint">{item.receivedAt ? new Date(item.receivedAt).toLocaleString("en-IN") : "time unavailable"}</p>
                      <p className="mt-0.5 text-[12px] font-medium text-text">{senderName(item.sender)}</p>
                      <p className="mt-0.5 text-[12px] text-text-muted">{item.subject || item.snippet}</p>
                    </li>
                  ))}
                </ol>
              </div>
            </div>

          </section>
        </div>
      )}
    </>
  );
}
