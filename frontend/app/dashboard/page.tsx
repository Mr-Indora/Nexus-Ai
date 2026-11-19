"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight, ArrowUpRight, BriefcaseBusiness, CalendarDays, CheckCircle2,
  CircleAlert, GraduationCap, Mail, RefreshCw, Sparkles, Target,
} from "lucide-react";
import AppShell from "@/components/AppShell";
import HoverContextPreview from "@/components/HoverContextPreview";
import { api } from "@/lib/api";
import { useNexus } from "@/lib/store";
import { dateKey, greeting, timeAgo, timeOf } from "@/lib/format";
import type { IntelligenceOverview } from "@/types";

interface StudyItem { id: string; title: string; summary: string; url: string | null; publishedAt: string | null; read?: boolean; }
interface StudyOverview {
  collegeNotices?: { items: StudyItem[]; lastCheckedAt: string | null; syncStatus: string; newNoticeCount: number; unreadCount: number };
  classroomUpdates?: { items: StudyItem[]; newUpdateCount: number; lastCheckedAt: string | null; lastAttemptAt: string | null; syncStatus: string; errorCode: string | null };
}
interface LinkedInOverview { connected: boolean; profile: { name: string; headline: string | null } | null; lastSyncedAt: string | null; }
interface CompletionTrend { available: boolean; days: { date: string; completed: number }[]; totalCompleted: number; message?: string; }
type Period = "today" | "week";
type FocusFilter = "all" | "attention" | "study" | "career";
interface PriorityItem {
  key: string;
  group: Exclude<FocusFilter, "all">;
  title: string;
  label: string;
  detail: string;
  summary: string;
  source: string;
  href: string;
  sourceUrl?: string;
  collegeNoticeId?: string;
  emailId?: string;
  score: number;
  timeline: { label: string; value: string }[];
}

function MetricBar({ value, total, color = "bg-primary" }: { value: number; total: number; color?: string }) {
  const width = total ? Math.max(5, Math.min(100, value / total * 100)) : 0;
  return <div className="h-1.5 overflow-hidden rounded-full bg-border"><div className={`h-full rounded-full ${color} transition-all duration-500`} style={{ width: `${width}%` }} /></div>;
}

export default function DashboardPage() {
  const { user, tasks, events, emails, sources, recommendation, syncing, message, sync } = useNexus();
  const [overview, setOverview] = useState<IntelligenceOverview | null>(null);
  const [study, setStudy] = useState<StudyOverview | null>(null);
  const [linkedin, setLinkedIn] = useState<LinkedInOverview | null>(null);
  const [trend, setTrend] = useState<CompletionTrend | null>(null);
  const [period, setPeriod] = useState<Period>("today");
  const [focusFilter, setFocusFilter] = useState<FocusFilter>("all");

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      api<IntelligenceOverview>("/intelligence/overview"),
      api<StudyOverview>("/pids/overview"),
      api<LinkedInOverview>("/linkedin/status"),
      api<CompletionTrend>("/analytics/task-completions?days=7"),
    ]).then(([i, s, l, t]) => {
      if (!active) return;
      if (i.status === "fulfilled") setOverview(i.value);
      if (s.status === "fulfilled") setStudy(s.value);
      if (l.status === "fulfilled") setLinkedIn(l.value);
      if (t.status === "fulfilled") setTrend(t.value);
    });
    return () => { active = false; };
  }, [syncing]);

  useEffect(() => {
    const refreshStudyCount = () => api<StudyOverview>("/pids/overview").then(setStudy).catch(() => {});
    const onStorage = (event: StorageEvent) => {
      if (event.key === "nexus-college-notices-read-change") refreshStudyCount();
    };
    window.addEventListener("nexus-college-notices-read", refreshStudyCount);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("nexus-college-notices-read", refreshStudyCount);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  const now = new Date();
  const todayKey = dateKey(now);
  const weekEnd = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
  const pending = tasks.filter((task) => task.status !== "completed");
  const completed = tasks.filter((task) => task.status === "completed");
  const overdue = pending.filter((task) => task.deadline && new Date(task.deadline) < now);
  const dueToday = pending.filter((task) => task.deadline && dateKey(new Date(task.deadline)) === todayKey);
  const highPriority = pending.filter((task) => task.priority === "high");
  const upcomingEvents = events.filter((event) => event.kind === "event" && new Date(event.start) >= now && new Date(event.start) < weekEnd);
  const todayEvents = upcomingEvents.filter((event) => dateKey(new Date(event.start)) === todayKey);
  const nextEvent = [...upcomingEvents].sort((a, b) => +new Date(a.start) - +new Date(b.start))[0];
  const actionableEmails = overview?.emails.actionable ?? emails.filter((email) => email.actionable).length;
  const replyRequests = overview?.emails.potentialResponseRequests ?? 0;
  const careerTasks = tasks.filter((task) => task.category === "Career");
  const openCareerTasks = careerTasks.filter((task) => task.status !== "completed").length;
  const selectedEvents = period === "today" ? todayEvents.length : upcomingEvents.length;
  const selectedDeadlines = period === "today" ? dueToday.length : pending.filter((task) => task.deadline && new Date(task.deadline) >= now && new Date(task.deadline) < weekEnd).length;
  const newestEmail = [...emails].sort((a, b) => Date.parse(b.receivedAt || "") - Date.parse(a.receivedAt || ""))[0];
  const latestNotice = study?.collegeNotices?.items.find((item) => !item.read) ?? study?.collegeNotices?.items[0];
  const latestClasswork = study?.classroomUpdates?.items[0];
  const firstName = user?.name.split(" ")[0] ?? "there";
  const totalTasks = pending.length + completed.length;
  const gmailSource = sources.find((source) => source.type === "gmail");
  const calendarSource = sources.find((source) => source.type === "calendar");
  const studyCount = (study?.collegeNotices?.unreadCount ?? 0) + (study?.classroomUpdates?.newUpdateCount ?? 0);
  const gmailSyncLabel = gmailSource?.lastSynced ? `Updated ${timeAgo(gmailSource.lastSynced)}` : "Waiting for first sync";
  const calendarSyncLabel = calendarSource?.lastSynced ? `Updated ${timeAgo(calendarSource.lastSynced)}` : "Waiting for first sync";
  const priorityItems: PriorityItem[] = [
    ...pending.map((task) => {
      const deadline = task.deadline ? new Date(task.deadline) : null;
      const isOverdue = Boolean(deadline && deadline < now);
      const isToday = Boolean(deadline && dateKey(deadline) === todayKey);
      const base = task.priority === "high" ? 70 : task.priority === "medium" ? 45 : 25;
      return {
        key: `task-${task.id}`, group: task.category === "Career" ? "career" as const : "attention" as const,
        title: task.title, label: isOverdue ? "Overdue" : isToday ? "Due today" : `${task.priority} priority`,
        detail: task.deadline ? `Due ${task.deadlineLabel}` : `${task.category} · no deadline`,
        summary: task.description || "Saved task in your workspace.", source: task.source === "gmail" ? "Gmail task" : task.source === "manual" ? "Your task" : task.source,
        href: `/tasks#task-${task.id}`, emailId: task.emailId || undefined,
        score: base + (isOverdue ? 35 : isToday ? 25 : deadline && deadline < weekEnd ? 10 : 0),
        timeline: [
          ...(task.createdAt ? [{ label: "Added", value: timeAgo(task.createdAt) }] : []),
          ...(task.deadline ? [{ label: "Deadline", value: task.deadlineLabel || new Date(task.deadline).toLocaleString() }] : []),
        ],
      };
    }),
    ...emails.filter((email) => email.actionable).map((email) => ({
      key: `email-${email.id}`, group: "attention" as const, title: email.subject || "Email needs attention", label: "Actionable email",
      detail: `${email.sender} · ${timeAgo(email.receivedAt)}`, summary: email.snippet || "Open the email to review its details.", source: "Gmail",
      href: "/intelligence", emailId: email.id, score: 66,
      timeline: [{ label: "Received", value: timeAgo(email.receivedAt) }],
    })),
    ...[...(study?.collegeNotices?.items ?? []).filter((item) => !item.read).slice(0, 3).map((item) => ({ item, source: "College notice" })), ...(study?.classroomUpdates?.items ?? []).slice(0, 3).map((item) => ({ item, source: "Google Classroom" }))].map(({ item, source }) => ({
      key: `study-${item.id}`, group: "study" as const, title: item.title, label: source,
      detail: item.publishedAt ? `Updated ${timeAgo(item.publishedAt)}` : "Recent study update", summary: item.summary || "Open the source to review the update.", source,
      href: "/intelligence", sourceUrl: item.url || undefined, score: item.publishedAt && now.getTime() - Date.parse(item.publishedAt) < 7 * 86400000 ? 54 : 34,
      collegeNoticeId: source === "College notice" ? item.id : undefined,
      timeline: item.publishedAt ? [{ label: "Published", value: timeAgo(item.publishedAt) }] : [],
    })),
    ...upcomingEvents.slice(0, 5).map((event) => ({
      key: `event-${event.id}`, group: "attention" as const, title: event.title, label: "Calendar event",
      detail: `Starts ${timeOf(new Date(event.start))}`, summary: event.location || "Upcoming event from your calendar.", source: "Google Calendar",
      href: "/calendar", sourceUrl: "/calendar", score: new Date(event.start).getTime() - now.getTime() < 24 * 60 * 60 * 1000 ? 62 : 42,
      timeline: [{ label: "Starts", value: new Date(event.start).toLocaleString() }],
    })),
    ...(linkedin?.connected && !linkedin.profile?.headline ? [{
      key: "linkedin-headline", group: "career" as const, title: "Add a professional headline", label: "LinkedIn profile",
      detail: "Profile improvement", summary: "Your connected LinkedIn profile does not currently include a headline.", source: "LinkedIn",
        href: "/linkedin", sourceUrl: "/linkedin", score: 20,
      timeline: linkedin.lastSyncedAt ? [{ label: "Profile checked", value: timeAgo(linkedin.lastSyncedAt) }] : [],
    }] : []),
  ].sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  const visibleFocusItems = priorityItems.filter((item) => focusFilter === "all" || item.group === focusFilter).slice(0, 5);

  const markCollegeNoticeRead = async (id: string) => {
    try {
      await api(`/pids/college-notices/${encodeURIComponent(id)}/read`, { method: "POST" });
      const latest = await api<StudyOverview>("/pids/overview");
      setStudy(latest);
      window.localStorage.setItem("nexus-college-notices-read-change", id);
      window.dispatchEvent(new Event("nexus-college-notices-read"));
    } catch { /* The next sync refreshes the summary if this read could not be saved. */ }
  };

  return <AppShell title="Home">
    <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-primary"><Sparkles size={14} /> Your personal workspace</div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-text sm:text-3xl">{greeting()}, {firstName}.</h1>
        <p className="mt-1.5 text-sm text-text-muted">Your day across tasks, messages, calendar, study sources, and career.{user?.lastSyncedAt && <span className="text-text-faint"> Last synced {timeAgo(user.lastSyncedAt)}.</span>} <span className="text-text-faint">Auto-syncs every 5 minutes while Nexus is open.</span></p>
      </div>
      <button onClick={() => void sync()} disabled={syncing} className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover disabled:opacity-60"><RefreshCw size={14} className={syncing ? "animate-spin" : ""} />{syncing ? "Syncing…" : "Sync workspace"}</button>
    </header>

    {message && <div role="status" className="mb-4 rounded-xl border border-border bg-surface px-4 py-3 text-xs text-text-muted">{message}</div>}

    <section className={`mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border px-4 py-3.5 ${overdue.length || dueToday.length || highPriority.length ? "border-high/25 bg-high-bg/50" : "border-primary/20 bg-primary/5"}`}>
      <div className="flex min-w-0 items-center gap-3"><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${overdue.length ? "bg-high/10 text-high" : "bg-primary/10 text-primary"}`}>{overdue.length ? <CircleAlert size={18} /> : <Target size={18} />}</span><div className="min-w-0"><p className="text-sm font-semibold text-text">{overdue.length ? `${overdue.length} overdue task${overdue.length === 1 ? "" : "s"} need attention` : dueToday.length ? `${dueToday.length} task${dueToday.length === 1 ? "" : "s"} due today` : `${pending.length} open task${pending.length === 1 ? "" : "s"} in your workspace`}</p><p className="mt-0.5 truncate text-xs text-text-muted">{overview?.dailyDecision?.focus?.title || (nextEvent ? `Next calendar event: ${nextEvent.title} at ${timeOf(new Date(nextEvent.start))}` : "Your connected information is summarized below.")}</p></div></div>
      <Link href={overdue.length || dueToday.length ? "/tasks" : "/calendar"} className="shrink-0 text-xs font-semibold text-primary hover:underline">{overdue.length || dueToday.length ? "Review tasks" : "Open calendar"} <ArrowRight size={13} className="inline" /></Link>
    </section>

    <section className="mb-5 rounded-2xl border border-border bg-surface p-4 shadow-sm sm:p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="font-display text-lg font-semibold text-text">Workspace overview</h2><p className="mt-0.5 text-xs text-text-muted">A quick read here; open any area for the full detail.</p></div>
        <div role="group" aria-label="Overview time period" className="flex rounded-lg border border-border bg-surface-muted p-1">
          {(["today", "week"] as const).map((option) => <button key={option} type="button" aria-pressed={period === option} onClick={() => setPeriod(option)} className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${period === option ? "bg-surface text-primary shadow-sm" : "text-text-muted hover:text-text"}`}>{option === "today" ? "Today" : "7 days"}</button>)}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-5">
        <Link href="/tasks" className="group rounded-xl border border-border p-4 transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
          <div className="flex items-center justify-between"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary"><CheckCircle2 size={17} /></span><ArrowUpRight size={15} className="text-text-faint transition group-hover:text-primary" /></div>
          <p className="mt-3 text-xs font-medium text-text-muted">Tasks &amp; goals</p><p className="mt-1 text-3xl font-semibold text-text">{pending.length}<span className="ml-1 text-sm font-normal text-text-faint">open</span></p>
          <div className="mt-3 flex items-center justify-between text-[10px] text-text-muted"><span>{overdue.length} overdue</span><span>{selectedDeadlines} due {period === "today" ? "today" : "this week"}</span></div><MetricBar value={pending.length} total={totalTasks} />
        </Link>

        <Link href="/intelligence" className="group rounded-xl border border-border p-4 transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
          <div className="flex items-center justify-between"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600"><Mail size={17} /></span><ArrowUpRight size={15} className="text-text-faint transition group-hover:text-primary" /></div>
          <p className="mt-3 text-xs font-medium text-text-muted">Gmail insights</p><p className="mt-1 text-3xl font-semibold text-text">{actionableEmails}<span className="ml-1 text-sm font-normal text-text-faint">actionable</span></p>
          <p className="mt-2 truncate text-[11px] text-text-muted">{replyRequests ? `${replyRequests} messages may need a reply` : newestEmail?.subject || "No recent messages loaded"}</p><p className="mt-1 text-[10px] text-text-faint">{gmailSyncLabel}</p>
        </Link>

        <Link href="/calendar" className="group rounded-xl border border-border p-4 transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
          <div className="flex items-center justify-between"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-500/10 text-sky-600"><CalendarDays size={17} /></span><ArrowUpRight size={15} className="text-text-faint transition group-hover:text-primary" /></div>
          <p className="mt-3 text-xs font-medium text-text-muted">Calendar</p><p className="mt-1 text-3xl font-semibold text-text">{selectedEvents}<span className="ml-1 text-sm font-normal text-text-faint">events</span></p>
          <p className="mt-2 truncate text-[11px] text-text-muted">{nextEvent ? `Next: ${nextEvent.title}` : "No upcoming events"}</p><p className="mt-1 text-[10px] text-text-faint">{calendarSyncLabel}</p>
        </Link>

        <Link href="/sources#college-notices" className="group rounded-xl border border-border p-4 transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
          <div className="flex items-center justify-between"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/10 text-amber-600"><GraduationCap size={18} /></span><ArrowUpRight size={15} className="text-text-faint transition group-hover:text-primary" /></div>
          <p className="mt-3 text-xs font-medium text-text-muted">College &amp; Classroom</p><p className="mt-1 text-3xl font-semibold text-text">{studyCount}<span className="ml-1 text-sm font-normal text-text-faint">to review</span></p>
          <p className="mt-2 truncate text-[11px] text-text-muted">{latestNotice?.title || latestClasswork?.title || (study?.classroomUpdates?.errorCode ? "Classroom needs attention" : "No new study updates")}</p><p className="mt-1 text-[10px] text-text-faint">{study?.collegeNotices?.lastCheckedAt ? `Notice check ${timeAgo(study.collegeNotices.lastCheckedAt)}` : "Open study insights"}</p>
        </Link>

        <Link href="/linkedin" className="group rounded-xl border border-border p-4 transition hover:-translate-y-0.5 hover:border-[#0A66C2]/40 hover:shadow-md">
          <div className="flex items-center justify-between"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#0A66C2]/10 text-[#0A66C2]"><BriefcaseBusiness size={17} /></span><ArrowUpRight size={15} className="text-text-faint transition group-hover:text-[#0A66C2]" /></div>
          <p className="mt-3 text-xs font-medium text-text-muted">Professional</p><p className="mt-1 truncate text-lg font-semibold text-text">{linkedin?.connected ? linkedin.profile?.name || "LinkedIn connected" : "LinkedIn not connected"}</p>
          <p className="mt-1 truncate text-[11px] text-text-muted">{linkedin?.profile?.headline || `${openCareerTasks} open career task${openCareerTasks === 1 ? "" : "s"}`}</p><p className="mt-1 text-[10px] text-text-faint">{linkedin?.lastSyncedAt ? `Profile ${timeAgo(linkedin.lastSyncedAt)}` : "Profile and career overview"}</p>
        </Link>
      </div>
    </section>

    <section aria-label="Sample workspace preview" className="mb-5 rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/5 via-surface to-surface p-4 shadow-sm sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2"><h2 className="font-display text-base font-semibold text-text">Workspace preview</h2><span className="rounded-full bg-primary/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-primary">Sample data</span></div><p className="mt-1 text-xs text-text-muted">Examples of how Nexus brings your sources together. These are illustrative only and never affect your live counts or account.</p></div>
        <span className="rounded-lg border border-border bg-surface/70 px-2.5 py-1.5 text-[10px] font-medium text-text-faint">Read-only examples</span>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {[
          { icon: CheckCircle2, label: "Task", title: "Submit DBMS assignment", detail: "Academic · High priority · Due tomorrow", tone: "bg-primary/10 text-primary" },
          { icon: Mail, label: "Gmail", title: "Interview availability requested", detail: "Career email · Reply may be needed", tone: "bg-emerald-500/10 text-emerald-600" },
          { icon: CalendarDays, label: "Calendar", title: "Placement preparation", detail: "Today · 2:00 PM · 90 minutes", tone: "bg-sky-500/10 text-sky-600" },
          { icon: GraduationCap, label: "College update", title: "Mid-semester timetable published", detail: "Study update · Open the original notice", tone: "bg-amber-500/10 text-amber-600" },
          { icon: BriefcaseBusiness, label: "Career", title: "A connection shared a new role", detail: "LinkedIn example · Review career updates", tone: "bg-[#0A66C2]/10 text-[#0A66C2]" },
        ].map((sample) => {
          const Icon = sample.icon;
          return <article key={sample.label} className="rounded-xl border border-border bg-surface/80 p-3">
            <div className="flex items-center gap-2"><span className={`flex h-7 w-7 items-center justify-center rounded-lg ${sample.tone}`}><Icon size={14} /></span><span className="text-[10px] font-semibold uppercase tracking-wide text-text-faint">{sample.label}</span><span className="ml-auto rounded-full bg-surface-muted px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wide text-text-faint">Sample</span></div>
            <p className="mt-2 line-clamp-2 text-xs font-semibold text-text">{sample.title}</p><p className="mt-1 line-clamp-2 text-[10px] leading-4 text-text-muted">{sample.detail}</p>
          </article>;
        })}
      </div>
    </section>

    <section className="mb-5 rounded-2xl border border-border bg-surface p-4 shadow-sm sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="font-display text-base font-semibold text-text">Top 5 workspace priorities</h2><p className="mt-1 text-xs text-text-muted">Tasks, email, calendar, study updates, and LinkedIn profile actions ranked together. Hover or focus an item for its preview and timeline.</p></div>
        <div role="group" aria-label="Filter focus queue" className="flex flex-wrap gap-1 rounded-lg border border-border bg-surface-muted p-1">
          {([{ id: "all", label: "All" }, { id: "attention", label: "Needs attention" }, { id: "study", label: "Study" }, { id: "career", label: "Career" }] as const).map((item) => <button key={item.id} type="button" aria-pressed={focusFilter === item.id} onClick={() => setFocusFilter(item.id)} className={`rounded-md px-2.5 py-1.5 text-[11px] font-medium transition ${focusFilter === item.id ? "bg-surface text-primary shadow-sm" : "text-text-muted hover:text-text"}`}>{item.label}</button>)}
        </div>
      </div>
      {visibleFocusItems.length ? <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">{visibleFocusItems.map((item, index) => <HoverContextPreview key={item.key} title={item.title} description={item.summary} meta={[item.source, item.label, item.detail]} emailId={item.emailId} emailUrl={item.sourceUrl} onSourceOpen={item.collegeNoticeId ? () => void markCollegeNoticeRead(item.collegeNoticeId!) : undefined} sourceLabel={item.group === "study" ? "Open original update" : undefined} destinationUrl={item.href} destinationLabel={item.emailId ? "Open Gmail insights" : item.group === "study" ? "Open study insights" : item.group === "career" ? "Open career details" : item.key.startsWith("task-") ? "Open task" : "Open details"} timeline={item.timeline}>
        <div className="group relative min-w-0 cursor-pointer rounded-xl border border-border px-3.5 py-3 transition hover:border-primary/40 hover:bg-surface-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
        <div className="flex items-center justify-between gap-2"><span className="flex min-w-0 items-center gap-2"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-[10px] font-bold text-primary">{index + 1}</span><span className="truncate rounded-full bg-primary-dim px-2 py-0.5 text-[10px] font-semibold text-primary">{item.label}</span></span><ArrowUpRight size={13} className="shrink-0 text-text-faint group-hover:text-primary" /></div>
        <p className="mt-2 truncate text-sm font-semibold text-text">{item.title}</p><p className="mt-1 truncate text-[11px] text-text-muted">{item.detail}</p>
        </div>
      </HoverContextPreview>)}</div> : <div className="mt-4 rounded-xl border border-dashed border-border px-4 py-6 text-center"><p className="text-sm font-medium text-text">Nothing in this focus area yet</p><p className="mt-1 text-xs text-text-muted">As new items sync or tasks are added, they will show here.</p></div>}
    </section>

    <section className="mb-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(280px,0.8fr)]">
      <ProductivityPanel trend={trend} completed={completed.length} open={pending.length} />

      <article className="rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/5 to-surface p-5 shadow-sm">
        <div className="flex items-center gap-2"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary"><Sparkles size={17} /></span><div><h2 className="font-display text-base font-semibold text-text">Next best action</h2><p className="text-[10px] text-text-faint">From your saved tasks and calendar</p></div></div>
        <p className="mt-4 text-sm font-semibold leading-6 text-text">{overview?.dailyDecision?.focus?.title || recommendation?.text || (nextEvent ? `Prepare for ${nextEvent.title}` : "Choose one task to move forward today")}</p>
        <p className="mt-2 text-xs leading-5 text-text-muted">{overview?.dailyDecision?.focus?.reason || (nextEvent ? `Your next calendar event is ${timeOf(new Date(nextEvent.start))}.` : `${pending.length} open tasks and ${actionableEmails} actionable emails are in your workspace.`)}</p>
        <Link href={overview?.dailyDecision?.focus ? "/tasks" : "/intelligence"} className="mt-4 inline-flex items-center gap-1 text-xs font-semibold text-primary">See supporting details <ArrowRight size={13} /></Link>
      </article>
    </section>

    <footer className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-surface-muted/50 px-4 py-3 text-xs text-text-muted">
      <span>{completed.length} completed · {pending.length} open · {upcomingEvents.length} calendar events this week</span>
      <div className="flex flex-wrap gap-x-4 gap-y-2"><Link href="/tasks" className="font-semibold text-primary hover:underline">Tasks</Link><Link href="/intelligence" className="font-semibold text-primary hover:underline">Gmail &amp; study insights</Link><Link href="/calendar" className="font-semibold text-primary hover:underline">Calendar</Link><Link href="/linkedin" className="font-semibold text-primary hover:underline">LinkedIn</Link><Link href="/sources" className="font-semibold text-primary hover:underline">Connections</Link></div>
    </footer>
  </AppShell>;
}

function ProductivityPanel({ trend, completed, open }: { trend: CompletionTrend | null; completed: number; open: number }) {
  const days = trend?.available ? trend.days : [];
  const total = days.reduce((sum, day) => sum + day.completed, 0);
  const activeDays = days.filter((day) => day.completed > 0).length;
  const peak = days.reduce<CompletionTrend["days"][number] | null>((best, day) => day.completed > (best?.completed ?? 0) ? day : best, null);
  const max = Math.max(1, ...days.map((day) => day.completed));
  const width = 640;
  const top = 22;
  const bottom = 142;
  const left = 38;
  const right = 12;
  const points = days.map((day, index) => {
    const x = left + ((width - left - right) * index) / Math.max(1, days.length - 1);
    const y = bottom - (day.completed / max) * (bottom - top);
    return { ...day, x, y };
  });
  const line = points.map((point) => `${point.x},${point.y}`).join(" ");
  const area = points.length ? `M ${points[0].x} ${bottom} L ${points.map((point) => `${point.x} ${point.y}`).join(" L ")} L ${points[points.length - 1].x} ${bottom} Z` : "";
  const tickValues = [...new Set([max, Math.ceil(max / 2), 0])];

  return <article className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
      <div><div className="flex items-center gap-2"><h2 className="font-display text-base font-semibold text-text">Productivity trend</h2><span className="rounded-full bg-primary-dim px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-primary">7 days</span></div><p className="mt-1 text-xs text-text-muted">Your completed tasks, day by day</p></div>
      <Link href="/tasks" className="text-xs font-semibold text-primary hover:underline">Task details <ArrowUpRight size={13} className="inline" /></Link>
    </div>

    {trend?.available && points.length ? <>
      <div className="mt-4 grid grid-cols-3 divide-x divide-border border-y border-border bg-surface-muted/35">
        <div className="px-4 py-3 sm:px-5"><p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-text-faint">Completed</p><p className="mt-1 text-xl font-semibold text-text">{total}</p><p className="text-[10px] text-text-muted">in the last 7 days</p></div>
        <div className="px-4 py-3 sm:px-5"><p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-text-faint">Active days</p><p className="mt-1 text-xl font-semibold text-text">{activeDays}<span className="ml-1 text-xs font-normal text-text-faint">/ 7</span></p><p className="text-[10px] text-text-muted">with a completion</p></div>
        <div className="px-4 py-3 sm:px-5"><p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-text-faint">Top day</p><p className="mt-1 truncate text-sm font-semibold text-text">{peak?.completed ? new Date(`${peak.date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : "—"}</p><p className="text-[10px] text-text-muted">{peak?.completed ? `${peak.completed} completed` : "No activity yet"}</p></div>
      </div>

      <div className="px-3 pt-4 sm:px-5">
        <div role="img" aria-label={`Task completions across seven days. ${total} tasks completed on ${activeDays} active days.`}>
          <svg viewBox={`0 0 ${width} 166`} preserveAspectRatio="none" className="h-36 w-full overflow-visible sm:h-40">
            <defs><linearGradient id="productivity-area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--primary)" stopOpacity="0.28" /><stop offset="100%" stopColor="var(--primary)" stopOpacity="0.015" /></linearGradient></defs>
            {tickValues.map((value) => {
              const y = bottom - (value / max) * (bottom - top);
              return <g key={value}><line x1={left} x2={width - right} y1={y} y2={y} stroke="var(--border-strong)" strokeDasharray={value === 0 ? "0" : "3 5"} strokeWidth="1" /><text x="0" y={y + 3} fill="var(--text-faint)" fontSize="10">{value}</text></g>;
            })}
            <path d={area} fill="url(#productivity-area)" />
            {points.length > 1 && <polyline points={line} fill="none" stroke="var(--primary)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
            {points.map((point) => <g key={point.date}><title>{`${point.completed} task${point.completed === 1 ? "" : "s"} completed on ${new Date(`${point.date}T12:00:00`).toLocaleDateString()}`}</title><circle cx={point.x} cy={point.y} r={point.completed ? 5 : 3} fill={point.completed ? "var(--primary)" : "var(--surface-muted)"} stroke="var(--surface)" strokeWidth="2" vectorEffect="non-scaling-stroke" /></g>)}
          </svg>
        </div>
        <div className="-mt-1 grid grid-cols-7 gap-1 pb-4">
          {points.map((day) => <div key={day.date} className="text-center" title={`${day.completed} completed`}><p className="text-[9px] font-medium text-text-muted">{new Date(`${day.date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" })}</p><p className={`mt-0.5 text-[10px] font-semibold ${day.completed ? "text-primary" : "text-text-faint"}`}>{day.completed}</p></div>)}
        </div>
      </div>
      {total === 0 && <p className="border-t border-border px-5 py-2.5 text-[10px] text-text-faint">No tasks were marked complete in this period. This chart uses recorded completion history.</p>}
    </> : <div className="p-5">
      <div className="flex items-end justify-between gap-4"><div><p className="text-[10px] font-medium uppercase tracking-[0.12em] text-text-faint">Current task progress</p><p className="mt-1 text-2xl font-semibold text-text">{completed + open}<span className="ml-1 text-xs font-normal text-text-muted">saved tasks</span></p></div><p className="text-right text-[10px] text-text-muted">{trend?.message || "Completion history will appear here after it becomes available."}</p></div>
      <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-surface-muted" aria-label={`${completed} completed, ${open} open tasks`}><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${completed + open ? completed / (completed + open) * 100 : 0}%` }} /></div>
      <div className="mt-2 flex flex-wrap justify-between gap-2 text-[10px]"><span className="font-medium text-primary">{completed} completed</span><span className="text-text-muted">{open} still open</span></div>
    </div>}
  </article>;
}
