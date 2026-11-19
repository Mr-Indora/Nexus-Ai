"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, Check, CircleAlert, Clock3, Loader2, RefreshCw, X } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api, ApiError } from "@/lib/api";
import { useNexus } from "@/lib/store";

interface PidsEmail {
  id: string;
  sender: string;
  subject: string;
  threadId: string;
  receivedAt: string | null;
  analysis: {
    categories?: string[];
    importance?: string;
    summary?: string;
    why_it_matters?: string;
    what_sender_expects?: string;
    reply_state?: string;
    reply_reason?: string | null;
    next_step?: string;
    actions?: { title: string; description: string; due_date?: string | null; evidence: string }[];
    important_dates?: { date?: string | null; evidence?: string; deadline?: boolean }[];
  } | null;
}

interface PidsThread {
  id: string;
  threadId: string;
  latestMessageAt: string;
  analysis: {
    summary?: string;
    reply_state?: string;
    deadlines?: string[];
    superseded_deadlines?: string[];
    pending_actions?: string[];
    resolved_actions?: string[];
    contradictions?: string[];
  };
}

interface PidsTask {
  id: string;
  title: string;
  description: string;
  priority: string;
  status?: string;
  dueDate: string | null;
  reviewState: string;
  evidence: { excerpt?: string; nexusTaskId?: number; url?: string; source_kind?: string };
}

interface PidsRecommendation {
  id: string;
  title: string;
  description: string;
  reason: string;
  priority: string;
  suggestedAction: string;
  evidence: { sender?: string; subject?: string; url?: string; source_kind?: string; published_at?: string | null; due_date?: string | null; content_extracted?: boolean; course?: string; notice_number?: string };
}

function recommendationContext(item: PidsRecommendation) {
  const subject = item.evidence.subject || item.title;
  const sender = (item.evidence.sender || "").split("<")[0].trim();
  const context = `${subject} ${sender} ${item.evidence.source_kind ?? ""}`.toLowerCase();
  if (/(job|career|hiring|recruit|interview|intern|resume|role)/i.test(context)) {
    const employer = subject.match(/(?:hr|hiring|recruitment)\s+from\s+(.+?)\s+(?:has|is|sent)/i)?.[1]?.trim();
    return {
      why: employer
        ? `${employer} sent you a hiring message${sender ? ` through ${sender}` : ""}. It may be relevant to your career plans.`
        : `${sender || "A recruiter or job service"} sent a career-related message that may be relevant to your plans.`,
      action: "Open the email to check the position and requirements. Follow up if it fits what you are looking for.",
    };
  }
  if (item.evidence.due_date) {
    return {
      why: `${subject} includes a date that may affect your schedule.`,
      action: `Check whether ${item.evidence.due_date} is still relevant, then add it to your task list if you need to act.`,
    };
  }
  return {
    why: `${subject} may be relevant to your plans. Review the details and decide whether it needs action.`,
    action: systemExplanationPattern.test(item.suggestedAction) || /(review the source message|open the source|review source)/i.test(item.suggestedAction)
      ? "Open the message, check the details, and decide whether you need to do anything."
      : item.suggestedAction,
  };
}

interface PidsEvent {
  id: string;
  title: string;
  startAt: string | null;
  endAt: string | null;
  eventDate: string | null;
  allDay: boolean;
  reviewState: string;
}

interface PidsOverview {
  generatedAt: string;
  analytics: {
    counts: { overdueByRecordedDate: number; dueTodayByRecordedDate: number; awaitingReplies: number; calendarOverlappingPairs: number };
    signals: { kind: string; priority: string; title: string; reason: string; evidence?: { taskCandidateId?: string; threadId?: string; threadRecordId?: string; latestMessageAt?: string | null; eventCandidateIds?: string[] } }[];
  };
  emails: { id: string; sender: string; subject: string; threadId: string; receivedAt: string | null }[];
  threads: PidsThread[];
  tasks: PidsTask[];
  recommendations: PidsRecommendation[];
  events: PidsEvent[];
  sources: { id: string; kind: string; displayName: string; status: string; sync: { status: string; lastSuccessAt: string | null; errorCode: string | null } | null }[];
  activeMemoryCount: number;
}
interface InboxEmail {
  id: string; sender: string; subject: string; snippet: string; receivedAt: string;
  actionable: boolean; taskId: string | null; engine: string;
}
interface EmailDetail {
  id: string;
  sender: string;
  subject: string;
  snippet: string;
  receivedAt: string;
  body: string;
  gmailUrl: string;
  timeline: { id: string; sender: string; subject: string; receivedAt: string; snippet: string }[];
}
type InsightsView = "all" | "actions" | "messages" | "calendar";

const panel = "rounded-xl border border-border bg-surface p-5 shadow-sm";
const systemExplanationPattern = /(deterministic|rule[- ]based|email rules|rules? engine|classifier|model output|analysis engine|confidence score|processing pipeline|algorithm|high impact|marked this message|review the source message|automatically flagged)/i;

function attentionKey(signal: PidsOverview["analytics"]["signals"][number]) {
  const evidence = signal.evidence;
  const sourceId = evidence?.taskCandidateId || (evidence?.threadRecordId ? `${evidence.threadRecordId}:${evidence.latestMessageAt || ""}` : null) || evidence?.threadId || evidence?.eventCandidateIds?.join(",") || signal.title.slice(0, 64);
  return `${signal.kind}:${sourceId}`;
}

function userFacingSignal(signal: PidsOverview["analytics"]["signals"][number]) {
  const personalCopy: Record<string, string> = {
    overdue_task: "This task is past its due date. Decide whether to finish it, update the date, or mark it complete.",
    task_due_today: "This task is due today. Consider setting aside time to finish it.",
    reply_requested: "This conversation may need a reply. Check the latest message and respond if needed.",
    date_changed: "A newer message may have changed the date. Check the latest details before planning.",
    calendar_conflict: "Two calendar commitments overlap. Review them and decide which one to prioritize.",
  };
  if (personalCopy[signal.kind]) return personalCopy[signal.kind];
  return systemExplanationPattern.test(signal.reason)
    ? `${signal.title} may need your attention. Open the related item to decide what to do.`
    : signal.reason;
}

function userFacingEmailSummary(email: PidsEmail, fallback = "Open the message to review its details.") {
  const summary = email.analysis?.summary?.trim();
  if (!summary || systemExplanationPattern.test(summary)) {
    const importance = email.analysis?.importance?.toLowerCase();
    return importance === "high" ? "This message may need timely attention." : fallback;
  }
  return summary;
}

function userFacingEmailWhy(email: PidsEmail) {
  const reason = email.analysis?.why_it_matters?.trim();
  if (reason && !systemExplanationPattern.test(reason)) return reason;
  const importance = email.analysis?.importance?.toLowerCase();
  return importance === "high"
    ? "This message may affect your current plans or need a timely response."
    : "Check whether this message has anything you need to act on.";
}

function userFacingEmailNextStep(email: PidsEmail) {
  const nextStep = email.analysis?.next_step?.trim();
  if (!nextStep || systemExplanationPattern.test(nextStep) || /review the source message|open the source/i.test(nextStep)) {
    return "Open the email and check whether it needs a response or follow-up.";
  }
  return nextStep;
}

async function fetchPidsWorkspace() {
  return Promise.all([
    api<PidsOverview>("/pids/overview"),
    api<PidsEmail[]>("/pids/emails?limit=20"),
    api<InboxEmail[]>("/emails").catch(() => [] as InboxEmail[]),
  ]);
}

function loadError(cause: unknown) {
  return cause instanceof ApiError && cause.status === 503
    ? "Your saved insights couldn’t be loaded. Please try again later."
    : "Could not load workspace insights. Check the backend connection and retry.";
}

function timeLabel(value: string | null) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Date unavailable" : date.toLocaleString();
}

export default function IntelligencePage() {
  const { user } = useNexus();
  const [overview, setOverview] = useState<PidsOverview | null>(null);
  const [emails, setEmails] = useState<PidsEmail[]>([]);
  const [inboxEmails, setInboxEmails] = useState<InboxEmail[]>([]);
  const [emailDetail, setEmailDetail] = useState<EmailDetail | null>(null);
  const [emailDetailLoading, setEmailDetailLoading] = useState(false);
  const [emailDetailError, setEmailDetailError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [taskEdits, setTaskEdits] = useState<Record<string, { title: string; dueDate: string }>>({});
  const [editingCandidateId, setEditingCandidateId] = useState<string | null>(null);
  const [dismissedAttention, setDismissedAttention] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [view, setView] = useState<InsightsView>("all");
  const [emailQuery, setEmailQuery] = useState("");
  const [emailImportance, setEmailImportance] = useState("all");
  const [emailReply, setEmailReply] = useState("all");
  const [emailCategory, setEmailCategory] = useState("all");

  const attentionStorageKey = user ? `nexus-attention-dismissed-${user.id}` : null;
  useEffect(() => {
    if (!attentionStorageKey) return;
    let active = true;
    void Promise.resolve().then(() => {
      if (!active) return;
      try {
        const stored = JSON.parse(window.localStorage.getItem(attentionStorageKey) || "[]");
        setDismissedAttention(Array.isArray(stored) ? stored.filter((item): item is string => typeof item === "string") : []);
      } catch { setDismissedAttention([]); }
    });
    return () => { active = false; };
  }, [attentionStorageKey]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [summary, messages, inbox] = await fetchPidsWorkspace();
      setOverview(summary);
      setEmails(messages);
      setInboxEmails(inbox);
    } catch (cause) {
      setError(loadError(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    fetchPidsWorkspace().then(([summary, messages, inbox]) => {
      if (active) {
        setOverview(summary);
        setEmails(messages);
        setInboxEmails(inbox);
      }
    }).catch((cause: unknown) => {
      if (active) setError(loadError(cause));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const matchingInboxEmail = (message: PidsEmail) => {
    const matches = inboxEmails.filter((item) => item.sender.trim().toLowerCase() === message.sender.trim().toLowerCase() && item.subject.trim().toLowerCase() === message.subject.trim().toLowerCase());
    if (!matches.length) return undefined;
    const targetReceivedAt = message.receivedAt;
    if (!targetReceivedAt) return matches[0];
    const distanceFromAnalyzedMessage = (receivedAt: string | null) => receivedAt
      ? Math.abs(Date.parse(receivedAt) - Date.parse(targetReceivedAt))
      : Number.POSITIVE_INFINITY;
    return matches.sort((a, b) => distanceFromAnalyzedMessage(a.receivedAt) - distanceFromAnalyzedMessage(b.receivedAt))[0];
  };

  const showEmailDetail = async (message: PidsEmail) => {
    const inboxMessage = matchingInboxEmail(message);
    if (!inboxMessage) {
      setEmailDetailError("Full message details are not available for this item yet. Sync your inbox, then try again.");
      return;
    }
    setEmailDetail(null);
    setEmailDetailError("");
    setEmailDetailLoading(true);
    try {
      setEmailDetail(await api<EmailDetail>(`/emails/${encodeURIComponent(inboxMessage.id)}`));
    } catch {
      setEmailDetailError("Could not load this email and its timeline. Please try again.");
    } finally {
      setEmailDetailLoading(false);
    }
  };

  const filteredMessages = emails.filter((email) => {
    const text = `${email.sender} ${email.subject} ${email.analysis?.summary ?? ""} ${email.analysis?.why_it_matters ?? ""}`.toLowerCase();
    if (!text.includes(emailQuery.trim().toLowerCase())) return false;
    const importance = email.analysis?.importance?.toLowerCase() ?? "unknown";
    if (emailImportance !== "all" && importance !== emailImportance) return false;
    const replyState = email.analysis?.reply_state?.toLowerCase() ?? "unknown";
    const needsReply = replyState.includes("reply") && !replyState.includes("no_reply") && !replyState.includes("not_requested") && replyState !== "unknown";
    const knownNoReply = ["no_reply", "not_requested", "not_needed", "none", "no_response_requested"].includes(replyState);
    if (emailReply === "needs_reply" && !needsReply) return false;
    if (emailReply === "no_reply" && !knownNoReply) return false;
    if (emailCategory !== "all" && !(email.analysis?.categories ?? []).some((category) => category.toLowerCase() === emailCategory)) return false;
    return true;
  });
  const emailCategories = [...new Set(emails.flatMap((email) => email.analysis?.categories ?? []).map((category) => category.toLowerCase()))].sort();
  const emailImportanceOptions = [...new Set(emails.map((email) => email.analysis?.importance?.toLowerCase()).filter((value): value is string => Boolean(value)))].sort();
  const visibleAttentionSignals = overview?.analytics.signals.filter((signal) => !dismissedAttention.includes(attentionKey(signal))) ?? [];
  const attentionCounts = {
    overdue: visibleAttentionSignals.filter((signal) => signal.kind === "overdue_task").length,
    dueToday: visibleAttentionSignals.filter((signal) => signal.kind === "task_due_today").length,
    replies: visibleAttentionSignals.filter((signal) => signal.kind === "reply_requested").length,
    overlaps: visibleAttentionSignals.filter((signal) => signal.kind === "calendar_conflict").length,
  };
  const taskReviewCount = overview?.tasks.filter((task) => task.reviewState === "needs_review").length ?? 0;

  const review = async (id: string, action: "approve" | "reject" | "dismiss") => {
    setBusyId(id);
    setError("");
    setNotice("");
    try {
      await api(`/pids/recommendations/${encodeURIComponent(id)}/review`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      setNotice(action === "approve" ? "Marked reviewed. This suggestion is removed from your pending list." : action === "reject" ? "Marked not relevant. This suggestion is removed from your pending list." : "Suggestion dismissed from your pending list.");
      await refresh();
    } catch {
      setError("The recommendation could not be updated. Refresh and try again.");
    } finally {
      setBusyId(null);
    }
  };

  const reviewTask = async (task: PidsTask, action: "approve" | "reject") => {
    const correction = taskEdits[task.id] ?? { title: task.title, dueDate: task.dueDate ?? "" };
    setBusyId(task.id);
    setError("");
    setNotice("");
    try {
      await api(`/pids/tasks/${encodeURIComponent(task.id)}/review`, {
        method: "POST",
        body: JSON.stringify({
          action,
          ...(action === "approve" ? { correction: {
            title: correction.title,
            ...(correction.dueDate ? { due_date: correction.dueDate } : {}),
          } } : {}),
        }),
      });
      setNotice(action === "approve" ? "Task candidate approved with your corrections." : "Task candidate rejected.");
      await refresh();
    } catch {
      setError("The task candidate could not be updated. Refresh and try again.");
    } finally {
      setBusyId(null);
    }
  };

  const createTask = async (task: PidsTask) => {
    setBusyId(task.id);
    setError("");
    try {
      await api(`/pids/tasks/${encodeURIComponent(task.id)}/create-task`, { method: "POST" });
      setNotice("Approved candidate was added to your Nexus task list.");
      await refresh();
    } catch {
      setError("The approved candidate could not be added to Nexus tasks.");
    } finally {
      setBusyId(null);
    }
  };

  const dismissSignal = (signal: PidsOverview["analytics"]["signals"][number]) => {
    const key = attentionKey(signal);
    setDismissedAttention((current) => {
      const next = current.includes(key) ? current : [...current, key];
      if (attentionStorageKey) window.localStorage.setItem(attentionStorageKey, JSON.stringify(next));
      return next;
    });
  };

  const completeAttentionTask = async (signal: PidsOverview["analytics"]["signals"][number]) => {
    const taskId = signal.evidence?.taskCandidateId;
    if (!taskId) return;
    setBusyId(taskId);
    setError("");
    try {
      const result = await api<{ id: string; status: string }>(`/pids/tasks/${encodeURIComponent(taskId)}/complete`, { method: "POST" }, 30_000);
      if (result.status !== "completed") throw new Error("The task was not marked complete.");
      setOverview((current) => {
        if (!current) return current;
        const removedSignal = current.analytics.signals.find((item) => attentionKey(item) === attentionKey(signal));
        const counts = { ...current.analytics.counts };
        if (removedSignal?.kind === "overdue_task") counts.overdueByRecordedDate = Math.max(0, counts.overdueByRecordedDate - 1);
        if (removedSignal?.kind === "task_due_today") counts.dueTodayByRecordedDate = Math.max(0, counts.dueTodayByRecordedDate - 1);
        return {
          ...current,
          analytics: {
            ...current.analytics,
            counts,
            signals: current.analytics.signals.filter((item) => attentionKey(item) !== attentionKey(signal)),
          },
          tasks: current.tasks.map((task) => task.id === taskId ? { ...task, status: "completed", reviewState: "approved" } : task),
        };
      });
      setNotice("Marked complete. It has been removed from your active attention list.");
      void api<PidsOverview>("/pids/overview").then(setOverview).catch(() => {});
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "This task could not be marked complete. Refresh and try again.");
    } finally {
      setBusyId(null);
    }
  };

  const editAttentionTask = (signal: PidsOverview["analytics"]["signals"][number]) => {
    const taskId = signal.evidence?.taskCandidateId;
    const task = overview?.tasks.find((item) => item.id === taskId);
    if (!taskId || !task) return;
    setTaskEdits((current) => ({ ...current, [taskId]: current[taskId] ?? { title: task.title, dueDate: task.dueDate ?? "" } }));
    setEditingCandidateId(taskId);
    window.setTimeout(() => document.getElementById(`candidate-${taskId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  };

  const reviewEvent = async (event: PidsEvent, action: "approve" | "reject") => {
    setBusyId(event.id);
    try {
      await api(`/pids/events/${encodeURIComponent(event.id)}/review`, {
        method: "POST", body: JSON.stringify({ action }),
      });
      setNotice(action === "approve" ? "Calendar event candidate approved." : "Calendar event candidate rejected.");
      await refresh();
    } catch {
      setError("The calendar event candidate could not be updated.");
    } finally {
      setBusyId(null);
    }
  };

  const deletePidsData = async () => {
    if (!window.confirm("Delete your saved email and study insights from this workspace? This cannot be undone.")) return;
    setBusyId("delete-data");
    try {
      await api("/pids/data", { method: "DELETE" });
      setNotice("Your saved insights were deleted.");
      await refresh();
    } catch {
      setError("Your saved insights could not be deleted. Please try again.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <AppShell title="Insights" breadcrumb="Nexus AI / Insights">
      <div className="mx-auto max-w-5xl space-y-5">
        <section className={`${panel} flex flex-wrap items-center justify-between gap-4`}>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-primary">Evidence first · Human reviewed</p>
            <h2 className="mt-2 font-display text-xl font-semibold text-text">Understand the conversation and what needs attention</h2>
            <p className="mt-1 text-sm text-text-muted">See useful actions and recommendations from your messages. Replies are never sent automatically.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => void deletePidsData()} disabled={busyId === "delete-data"} className="rounded-lg border border-high/30 px-3 py-2 text-sm font-medium text-high disabled:opacity-50">Delete saved insights</button>
            <button onClick={() => void refresh()} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium text-text hover:bg-surface-muted disabled:opacity-50">
              <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Refresh
            </button>
          </div>
        </section>

        {error && <div role="alert" className="flex items-start gap-2 rounded-lg border border-high/30 bg-high-bg px-4 py-3 text-sm text-high"><CircleAlert size={17} className="mt-0.5 shrink-0" />{error}</div>}
        {notice && <div role="status" className="rounded-lg border border-low/30 bg-low-bg px-4 py-3 text-sm text-low">{notice}</div>}

        {overview && <nav aria-label="Filter insights" className={`${panel} flex flex-wrap items-center gap-2`}>
          {([
            ["all", "Overview", overview.tasks.length + overview.recommendations.length + emails.length + overview.events.length],
            ["actions", "Actions", overview.tasks.length + overview.recommendations.length],
            ["messages", "Messages", emails.length + overview.threads.length],
            ["calendar", "Calendar", overview.events.length],
          ] as const).map(([id, label, count]) => <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)} className={`rounded-full border px-3.5 py-2 text-xs font-semibold transition ${view === id ? "border-primary bg-primary text-white" : "border-border text-text-muted hover:border-primary/40 hover:text-text"}`}>{label}<span className={`ml-2 rounded-full px-1.5 py-0.5 text-[10px] ${view === id ? "bg-white/20 text-white" : "bg-surface-muted text-text-faint"}`}>{count}</span></button>)}
        </nav>}

        {loading && !overview ? (
          <div className={`${panel} flex items-center justify-center gap-3 py-14 text-sm text-text-muted`}><Loader2 size={18} className="animate-spin" /> Loading your insights…</div>
        ) : overview ? (
          <>
            <section aria-label="Insights summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div className={panel}><p className="text-xs text-text-faint">Needs attention</p><p className="mt-2 text-2xl font-semibold text-text">{visibleAttentionSignals.length}</p><p className="mt-1 text-[10px] text-text-faint">Tasks, replies and calendar conflicts</p></div>
              <div className={panel}><p className="text-xs text-text-faint">Tasks to review</p><p className="mt-2 text-2xl font-semibold text-text">{taskReviewCount}</p><p className="mt-1 text-[10px] text-text-faint">Approve, edit or reject</p></div>
              <div className={panel}><p className="text-xs text-text-faint">Replies requested</p><p className="mt-2 text-2xl font-semibold text-text">{attentionCounts.replies}</p><p className="mt-1 text-[10px] text-text-faint">Based on recent synced conversations</p></div>
              <div className={panel}><p className="text-xs text-text-faint">Recent messages</p><p className="mt-2 text-2xl font-semibold text-text">{emails.length}</p><p className="mt-1 text-[10px] text-text-faint">Messages available to explore</p></div>
            </section>

            {(view === "all" || view === "actions") && <>
            <section className={panel}>
              <h3 className="font-display text-lg font-semibold text-text">What needs attention</h3>
              <div className="mt-3 grid gap-2 sm:grid-cols-4">
                <div className="rounded-lg bg-surface-muted p-3"><p className="text-xs text-text-faint">Past due dates</p><p className="mt-1 text-xl font-semibold text-text">{attentionCounts.overdue}</p></div>
                <div className="rounded-lg bg-surface-muted p-3"><p className="text-xs text-text-faint">Due today</p><p className="mt-1 text-xl font-semibold text-text">{attentionCounts.dueToday}</p></div>
                <div className="rounded-lg bg-surface-muted p-3"><p className="text-xs text-text-faint">Replies requested</p><p className="mt-1 text-xl font-semibold text-text">{attentionCounts.replies}</p></div>
                <div className="rounded-lg bg-surface-muted p-3"><p className="text-xs text-text-faint">Calendar overlaps</p><p className="mt-1 text-xl font-semibold text-text">{attentionCounts.overlaps}</p></div>
              </div>
              {visibleAttentionSignals.length > 0 ? <ul className="mt-4 space-y-2">{visibleAttentionSignals.slice(0, 8).map((signal) => {
                const taskId = signal.evidence?.taskCandidateId;
                const taskAvailable = Boolean(taskId && overview.tasks.some((task) => task.id === taskId));
                const isTaskSignal = signal.kind === "overdue_task" || signal.kind === "task_due_today";
                const relatedHref = taskId ? `#candidate-${taskId}` : signal.kind === "calendar_conflict" ? "/calendar" : "#inbox-evidence";
                return <li key={attentionKey(signal)} className="flex flex-col gap-3 rounded-xl border border-border bg-background/40 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2 py-0.5 text-[9px] font-semibold ${signal.priority === "high" ? "bg-high-bg text-high" : "bg-primary-dim text-primary"}`}>{isTaskSignal ? "Task" : signal.kind === "reply_requested" ? "Email" : signal.kind === "calendar_conflict" ? "Calendar" : "Update"}</span><h4 className="line-clamp-2 text-sm font-semibold leading-5 text-text">{signal.title}</h4></div>
                    <p className="mt-1 text-xs leading-5 text-text-muted">{userFacingSignal(signal)}</p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <a href={relatedHref} className="rounded-lg border border-border px-3 py-2 text-[11px] font-medium text-text-muted hover:bg-surface-muted">{taskId ? "Edit details" : signal.kind === "calendar_conflict" ? "Open calendar" : "View messages"}</a>
                    {isTaskSignal && taskId && <button onClick={() => void completeAttentionTask(signal)} disabled={busyId === taskId} className="rounded-lg bg-primary px-3 py-2 text-[11px] font-semibold text-white disabled:opacity-50">{busyId === taskId ? "Saving…" : "Mark done"}</button>}
                    {!isTaskSignal && <button onClick={() => dismissSignal(signal)} className="rounded-lg px-2 py-2 text-[11px] font-medium text-text-faint hover:bg-surface-muted">Mark handled</button>}
                    {isTaskSignal && taskAvailable && <button onClick={() => editAttentionTask(signal)} className="rounded-lg px-2 py-2 text-[11px] font-medium text-text-faint hover:bg-surface-muted">Edit task</button>}
                  </div>
                </li>;
              })}</ul> : <p className="mt-4 rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-text-muted">Everything here is handled. New items will appear after the next sync.</p>}
              <p className="mt-3 text-[11px] text-text-faint">Based on the dates and times in your records; task duration is not included.</p>
            </section>

            <section className={panel}>
              <h3 className="font-display text-lg font-semibold text-text">Recommendations</h3>
              <p className="mt-1 text-sm text-text-muted">A short explanation of why something may matter to you, with a practical next step.</p>
              <p className="mt-2 rounded-lg border border-border bg-surface-muted/60 px-3 py-2 text-xs leading-5 text-text-muted">“Mark reviewed” only updates this suggestion in Insights. It does not create a task, reply to the email, or change anything in Gmail. Use Tasks &amp; Goals if you want to save a task.</p>
              {overview.recommendations.length === 0 ? <p className="mt-5 rounded-lg border border-dashed border-border-strong px-4 py-8 text-center text-sm text-text-muted">No recommendations need review.</p> :
                <div className="mt-4 divide-y divide-border">{overview.recommendations.map((item) => {
                  const context = recommendationContext(item);
                  return <article key={item.id} className="py-4 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-start justify-between gap-3"><div><h4 className="font-medium text-text">{item.title}</h4><p className="mt-1 text-sm text-text-muted">{systemExplanationPattern.test(item.description) ? item.title : item.description}</p></div><span className="rounded-full bg-primary-dim px-2.5 py-1 text-xs font-medium capitalize text-primary">{item.priority}</span></div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-surface-muted px-3 py-2.5"><p className="text-[10px] font-semibold uppercase tracking-wide text-text-faint">Why it may matter</p><p className="mt-1 text-sm leading-5 text-text-muted">{context.why}</p></div><div className="rounded-lg border border-primary/15 bg-primary/5 px-3 py-2.5"><p className="text-[10px] font-semibold uppercase tracking-wide text-primary">A useful next step</p><p className="mt-1 text-sm leading-5 text-text">{context.action}</p></div></div>
                  {(item.evidence.course || item.evidence.notice_number || item.evidence.published_at || item.evidence.due_date) && <p className="mt-1 text-xs text-text-faint">{[item.evidence.course, item.evidence.notice_number && `Notice ${item.evidence.notice_number}`, item.evidence.published_at && `Published ${new Date(item.evidence.published_at).toLocaleDateString()}`, item.evidence.due_date && `Due ${item.evidence.due_date}`].filter(Boolean).join(" · ")}</p>}
                  {item.evidence.url && <a href={item.evidence.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex text-xs font-medium text-primary underline underline-offset-2">{item.evidence.source_kind?.toLowerCase().includes("email") || item.evidence.source_kind?.toLowerCase().includes("gmail") ? "Open related email" : "Open related update"}</a>}
                  {item.evidence.content_extracted === false && <p className="mt-2 text-xs text-text-faint">The notice preview does not include the full text. Open the original notice to read it.</p>}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button onClick={() => void review(item.id, "approve")} disabled={busyId === item.id} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"><Check size={14} /> Mark reviewed</button>
                    <button onClick={() => void review(item.id, "reject")} disabled={busyId === item.id} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs font-medium text-text-muted disabled:opacity-50"><X size={14} /> Not relevant</button>
                    <button onClick={() => void review(item.id, "dismiss")} disabled={busyId === item.id} className="rounded-lg px-3 py-2 text-xs font-medium text-text-faint hover:bg-surface-muted disabled:opacity-50">Dismiss</button>
                  </div>
                </article>})}</div>}
            </section>

            </>}

            <section className="flex flex-col gap-5">
              {(view === "all" || view === "messages") &&
              <div className={`${panel} order-2`}>
                <h3 className="font-display text-lg font-semibold text-text">Conversation state</h3>
                <div className="mt-3 space-y-3">{overview.threads.length === 0 ? <p className="py-6 text-center text-sm text-text-muted">Thread summaries will appear after messages are synced.</p> : overview.threads.map((thread) => <article key={thread.id} className="rounded-lg border border-border p-3">
                  <div className="flex items-start justify-between gap-3"><p className="text-sm font-medium text-text">{thread.analysis.summary || `Thread ${thread.threadId}`}</p><span className="shrink-0 text-[11px] text-text-faint">{timeLabel(thread.latestMessageAt)}</span></div>
                  <p className="mt-2 text-xs text-text-muted">Reply state: {thread.analysis.reply_state?.replaceAll("_", " ") ?? "unknown"}</p>
                  {!!thread.analysis.pending_actions?.length && <p className="mt-1 text-xs text-text-muted">Pending: {thread.analysis.pending_actions.join(" · ")}</p>}
                  {!!thread.analysis.deadlines?.length && <p className="mt-1 text-xs text-text-muted">Dates: {thread.analysis.deadlines.join(" · ")}</p>}
                  {!!thread.analysis.superseded_deadlines?.length && <p className="mt-1 text-xs text-text-faint">Superseded dates: {thread.analysis.superseded_deadlines.join(" · ")}</p>}
                  {!!thread.analysis.contradictions?.length && <p className="mt-2 text-xs text-high">Needs clarification: {thread.analysis.contradictions.join(" ")}</p>}
                </article>)}</div>
              </div>}
              {(view === "all" || view === "actions") &&
              <div className={`${panel} order-1`}>
                <h3 className="font-display text-lg font-semibold text-text">Action candidates</h3>
                <p className="mt-1 text-sm text-text-muted">These remain review-only until you approve them in the task workflow.</p>
                <div className="mt-4 space-y-2">{overview.tasks.length === 0 ? <p className="py-6 text-center text-sm text-text-muted">No action candidates need review.</p> : overview.tasks.map((task) => <article id={`candidate-${task.id}`} key={task.id} className="scroll-mt-24 rounded-xl border border-border bg-background/40 p-3.5 sm:p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h4 className="line-clamp-2 text-sm font-semibold leading-5 text-text">{task.title}</h4><p className="mt-1 line-clamp-2 text-xs leading-5 text-text-muted">{task.description}</p></div><span className="shrink-0 rounded-full bg-surface-muted px-2.5 py-1 text-[10px] font-semibold capitalize text-text-muted">{task.priority}</span></div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-faint"><span className="inline-flex items-center gap-1"><Clock3 size={12} />{task.dueDate ? `Due ${task.dueDate}` : "No deadline found"}</span><span className="capitalize">{task.reviewState.replaceAll("_", " ")}</span></div>
                  <details className="mt-3 rounded-lg border border-border bg-surface px-3 py-2">
                    <summary className="cursor-pointer text-[11px] font-semibold text-text-muted">Source context</summary>
                    {task.evidence.excerpt && <blockquote className="mt-2 max-h-28 overflow-y-auto border-l-2 border-primary/40 pl-2 text-xs leading-5 text-text-muted">{task.evidence.excerpt}</blockquote>}
                    {task.evidence.url && <a href={task.evidence.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex text-xs font-medium text-primary underline underline-offset-2">Open assignment or notice</a>}
                  </details>
                  {task.reviewState === "needs_review" && <>
                    <details open={editingCandidateId === task.id} className="mt-2 rounded-lg border border-border bg-surface px-3 py-2">
                      <summary className="cursor-pointer text-[11px] font-semibold text-text-muted">Edit task title or deadline</summary>
                      <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px]">
                        <label className="text-[11px] font-medium text-text-faint">Task title
                          <input value={taskEdits[task.id]?.title ?? task.title} onChange={(event) => setTaskEdits((current) => ({ ...current, [task.id]: { title: event.target.value, dueDate: current[task.id]?.dueDate ?? task.dueDate ?? "" } }))} maxLength={256} className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-2 text-xs text-text" />
                        </label>
                        <label className="text-[11px] font-medium text-text-faint">Deadline
                          <input type="date" value={taskEdits[task.id]?.dueDate ?? task.dueDate ?? ""} onChange={(event) => setTaskEdits((current) => ({ ...current, [task.id]: { title: current[task.id]?.title ?? task.title, dueDate: event.target.value } }))} className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-2 text-xs text-text" />
                        </label>
                      </div>
                    </details>
                    <div className="mt-3 flex flex-wrap justify-end gap-2">
                      <button onClick={() => void reviewTask(task, "reject")} disabled={busyId === task.id} className="rounded-lg border border-border px-3.5 py-2 text-xs font-medium text-text-muted disabled:opacity-50">Reject</button>
                      <button onClick={() => void reviewTask(task, "approve")} disabled={busyId === task.id} className="rounded-lg bg-primary px-3.5 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve task</button>
                    </div>
                  </>}
                  {task.reviewState === "approved" && !task.evidence.nexusTaskId && <button onClick={() => void createTask(task)} disabled={busyId === task.id} className="mt-3 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Add to Nexus tasks</button>}
                  {task.evidence.nexusTaskId && <p className="mt-3 text-xs text-low">Added to Nexus tasks.</p>}
                </article>)}</div>
              </div>}
            </section>

            {(view === "all" || view === "calendar") && <section className={panel}>
              <h3 className="font-display text-lg font-semibold text-text">Calendar candidates</h3>
              <p className="mt-1 text-sm text-text-muted">Events imported from Google Calendar are reviewed here; importing does not write anything back to Google.</p>
              <div className="mt-3 space-y-2">{overview.events.length === 0 ? <p className="py-5 text-center text-sm text-text-muted">No calendar candidates need review.</p> : overview.events.map((event) => <article key={event.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
                <div><p className="text-sm font-medium text-text">{event.title}</p><p className="mt-1 text-xs text-text-faint">{event.allDay ? event.eventDate : timeLabel(event.startAt)}</p></div>
                <div className="flex gap-2"><button onClick={() => void reviewEvent(event, "approve")} disabled={busyId === event.id} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve</button><button onClick={() => void reviewEvent(event, "reject")} disabled={busyId === event.id} className="rounded-lg border border-border px-3 py-2 text-xs text-text-muted disabled:opacity-50">Reject</button></div>
              </article>)}</div>
            </section>}

            {(view === "all" || view === "messages") && <section id="inbox-evidence" className={panel}>
              <h3 className="font-display text-lg font-semibold text-text">Recent message analysis</h3>
              <p className="mt-1 text-sm text-text-muted">Search and filter the messages; hover for a short preview or open one to see its full details and timeline.</p>
              <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                <label className="sm:col-span-2 xl:col-span-1"><span className="sr-only">Search recent messages</span><input value={emailQuery} onChange={(event) => setEmailQuery(event.target.value)} placeholder="Search sender, subject, or summary…" className="w-full rounded-lg border border-border bg-background px-3.5 py-2.5 text-sm text-text placeholder:text-text-faint" /></label>
                <label className="text-[10px] font-medium text-text-faint">Importance<select value={emailImportance} onChange={(event) => setEmailImportance(event.target.value)} className="mt-1 block w-full rounded-lg border border-border bg-background px-3 py-2 text-xs text-text"><option value="all">All importance</option>{emailImportanceOptions.map((value) => <option key={value} value={value}>{value}</option>)}<option value="unknown">Not analyzed</option></select></label>
                <label className="text-[10px] font-medium text-text-faint">Reply status<select value={emailReply} onChange={(event) => setEmailReply(event.target.value)} className="mt-1 block w-full rounded-lg border border-border bg-background px-3 py-2 text-xs text-text"><option value="all">All reply status</option><option value="needs_reply">May need a reply</option><option value="no_reply">No reply flagged</option></select></label>
                <label className="text-[10px] font-medium text-text-faint">Category<select value={emailCategory} onChange={(event) => setEmailCategory(event.target.value)} className="mt-1 block w-full rounded-lg border border-border bg-background px-3 py-2 text-xs capitalize text-text"><option value="all">All categories</option>{emailCategories.map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
              </div>
              <div className="mt-4 flex items-center justify-between text-[11px] text-text-faint"><span>{filteredMessages.length} of {emails.length} messages</span><span>Newest first</span></div>
              <div className="mt-2 space-y-2">{emails.length === 0 ? <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-text-muted">No recent messages are available. Sync your workspace to refresh them.</p> : filteredMessages.length === 0 ? <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-text-muted">No messages match those filters. Try another category or clear the search.</p> : filteredMessages.map((email) => <article key={email.id} className="group relative rounded-xl border border-border bg-surface px-4 py-3 transition hover:border-primary/35 hover:bg-surface-muted/60">
                <div className="flex flex-wrap items-start justify-between gap-3"><button type="button" onClick={() => void showEmailDetail(email)} className="min-w-0 flex-1 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"><span className="block truncate text-sm font-semibold text-text">{email.subject || "(no subject)"}</span><span className="mt-1 block truncate text-xs text-text-muted">{email.sender} · {timeLabel(email.receivedAt)}</span></button><div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5"><span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold capitalize ${email.analysis?.importance === "high" ? "bg-high-bg text-high" : "bg-primary-dim text-primary"}`}>{email.analysis?.importance ?? "Unanalyzed"}</span>{email.analysis?.reply_state && <span className="rounded-full border border-border px-2.5 py-1 text-[10px] capitalize text-text-muted">{email.analysis.reply_state.replaceAll("_", " ")}</span>}<button type="button" onClick={() => void showEmailDetail(email)} className="rounded-lg px-2 py-1.5 text-[11px] font-semibold text-primary hover:bg-primary-dim">View details</button></div></div>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-text-muted">{userFacingEmailSummary(email, matchingInboxEmail(email)?.snippet || "Open the message to review its details.")}</p>
                {!!email.analysis?.categories?.length && <div className="mt-2 flex flex-wrap gap-1.5">{email.analysis.categories.map((category) => <span key={category} className="rounded-full border border-border px-2 py-0.5 text-[10px] capitalize text-text-faint">{category}</span>)}</div>}
                <div className="pointer-events-none absolute left-3 top-[calc(100%-2px)] z-30 hidden w-[min(26rem,calc(100vw-4rem))] rounded-xl border border-border bg-surface p-4 shadow-xl group-hover:block group-focus-within:block"><p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Message preview · {email.sender}</p><p className="mt-1 text-sm font-semibold text-text">{email.subject || "(no subject)"}</p><p className="mt-2 line-clamp-4 text-xs leading-5 text-text-muted">{userFacingEmailSummary(email, matchingInboxEmail(email)?.snippet || "Open this message for the full content.")}</p>{email.analysis?.why_it_matters && <p className="mt-2 border-l-2 border-primary/40 pl-2 text-xs leading-5 text-text-muted"><span className="font-semibold text-text">Why it may matter:</span> {userFacingEmailWhy(email)}</p>}{email.analysis?.next_step && <p className="mt-2 text-xs text-text"><span className="font-semibold">A useful next step:</span> {userFacingEmailNextStep(email)}</p>}<p className="mt-3 text-[10px] text-text-faint">Received {timeLabel(email.receivedAt)} · Open details for the conversation timeline</p></div>
              </article>)}</div>
            </section>}
          </>
        ) : null}
        {(emailDetailLoading || emailDetailError || emailDetail) && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-3 sm:p-6" onClick={() => { if (!emailDetailLoading) { setEmailDetail(null); setEmailDetailError(""); } }}>
          <section role="dialog" aria-modal="true" aria-labelledby="email-detail-title" className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4"><div className="min-w-0"><p className="text-[10px] font-semibold uppercase tracking-wide text-primary">Email details &amp; timeline</p><h2 id="email-detail-title" className="mt-1 truncate font-display text-lg font-semibold text-text">{emailDetail?.subject || (emailDetailLoading ? "Loading email…" : "Email details")}</h2>{emailDetail && <p className="mt-1 truncate text-xs text-text-muted">{emailDetail.sender} · {timeLabel(emailDetail.receivedAt)}</p>}</div><button type="button" aria-label="Close email details" onClick={() => { setEmailDetail(null); setEmailDetailError(""); }} className="rounded-lg border border-border p-2 text-text-muted hover:bg-surface-muted"><X size={16} /></button></header>
            <div className="max-h-[calc(90vh-76px)] overflow-y-auto p-5">{emailDetailLoading ? <div className="flex items-center gap-2 py-10 text-sm text-text-muted"><Loader2 size={16} className="animate-spin" />Loading the email and its timeline…</div> : emailDetailError ? <p role="alert" className="rounded-lg border border-high/30 bg-high-bg px-4 py-3 text-sm text-high">{emailDetailError}</p> : emailDetail && <>
              <div className="rounded-xl border border-border bg-surface-muted/60 p-4"><p className="text-xs font-semibold text-text">Message</p><div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-text-muted">{emailDetail.body || emailDetail.snippet || "Message content is unavailable."}</div></div>
              <div className="mt-5"><h3 className="font-display text-sm font-semibold text-text">Conversation timeline</h3><div className="mt-3 border-l border-border pl-4">{[...emailDetail.timeline].sort((a, b) => Date.parse(a.receivedAt) - Date.parse(b.receivedAt)).map((item) => <article key={item.id} className="relative pb-4 last:pb-0"><span className="absolute -left-[21px] top-1 h-3 w-3 rounded-full border-2 border-surface bg-primary"/><p className="text-[10px] font-medium text-text-faint">{timeLabel(item.receivedAt)}</p><p className="mt-1 text-sm font-semibold text-text">{item.sender}</p><p className="mt-0.5 text-xs font-medium text-text-muted">{item.subject}</p>{item.snippet && <p className="mt-1 line-clamp-3 text-xs leading-5 text-text-faint">{item.snippet}</p>}</article>)}</div><p className="mt-3 text-[10px] text-text-faint">Timeline contains messages currently synced to Nexus.</p></div>
              <a href={emailDetail.gmailUrl} target="_blank" rel="noreferrer" className="mt-5 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-xs font-semibold text-white hover:bg-primary-hover">Open this email in Gmail <ArrowUpRight size={14}/></a>
            </>}</div>
          </section>
        </div>}
      </div>
    </AppShell>
  );
}
