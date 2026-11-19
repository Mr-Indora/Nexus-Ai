"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { useNexus } from "@/lib/store";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  BriefcaseBusiness,
  Check,
  Clock3,
  ExternalLink,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  UserRound,
} from "lucide-react";

interface LinkedInStatus {
  configured: boolean;
  connected: boolean;
  status: string;
  lastAttemptAt: string | null;
  lastSyncedAt: string | null;
  lastErrorCode: string | null;
  profile: {
    name: string;
    givenName: string;
    familyName: string;
    headline: string | null;
    email: string | null;
    emailVerified: boolean | null;
    pictureUrl: string | null;
    locale: string | null;
  } | null;
}
function formatTime(value: string | null) {
  if (!value) return "Not synced yet";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Time unavailable" : date.toLocaleString();
}

function statusMessage(code: string | null) {
  if (!code) return null;
  const messages: Record<string, string> = {
    database_update_required: "One small database update is needed before LinkedIn can be connected. Follow the migration steps in README.",
    not_configured: "Add the LinkedIn Client ID and Client Secret in backend/.env, then restart Nexus.",
    linkedin_not_configured: "Add the LinkedIn Client ID and Client Secret in backend/.env, then restart Nexus.",
    real_account_required: "Sign in with your Google account to connect your LinkedIn profile.",
    invalid_state: "This connection request expired. Please start again.",
    authorization_cancelled: "LinkedIn access was not approved. You can try again whenever you are ready.",
    authorization_failed: "LinkedIn could not complete the connection. Please try again.",
    linkedin_id_token_invalid: "LinkedIn sign-in finished, but Nexus could not verify the profile response. Please connect again.",
    linkedin_app_mismatch: "The LinkedIn app ID in Nexus does not match the app that issued this sign-in. Check the LinkedIn Client ID in backend/.env.",
    linkedin_id_token_expired: "LinkedIn sign-in expired before Nexus could finish. Please connect again.",
    linkedin_signing_key_unavailable: "Nexus could not reach LinkedIn to verify the sign-in. Please try again shortly.",
    linkedin_id_token_signature_invalid: "LinkedIn returned a profile token that Nexus could not verify. Recheck the LinkedIn app credentials and try again.",
    linkedin_id_token_claims_missing: "LinkedIn returned an incomplete sign-in response. Confirm OpenID Connect is enabled for this app, then try again.",
    linkedin_connect_unavailable: "Nexus could not start the LinkedIn connection just now. Please wait a moment and try again.",
    linkedin_reconnect_required: "Your LinkedIn permission needs refreshing. Reconnect your profile.",
    linkedin_permission_denied: "LinkedIn did not allow this profile request. Check the permissions approved for your app.",
    linkedin_rate_limited: "LinkedIn is receiving too many requests. Try again shortly.",
    linkedin_temporarily_unavailable: "LinkedIn could not be reached. Try again shortly.",
    linkedin_sync_failed: "The profile refresh did not finish. Please try again.",
  };
  return messages[code] ?? "LinkedIn could not be updated. Please reconnect and try again.";
}

function Metric({ label, value, hint, icon: Icon }: { label: string; value: string; hint: string; icon: typeof UserRound }) {
  return <article className="rounded-2xl border border-border bg-surface p-4 shadow-sm sm:p-5">
    <div className="flex items-center justify-between"><span className="text-xs font-medium text-text-muted">{label}</span><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-dim text-primary"><Icon size={17} /></span></div>
    <p className="mt-3 text-lg font-semibold text-text">{value}</p>
    <p className="mt-1 text-[11px] text-text-faint">{hint}</p>
  </article>;
}

export default function LinkedInPage() {
  const { tasks } = useNexus();
  const [status, setStatus] = useState<LinkedInStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api<LinkedInStatus>("/linkedin/status")
      .then((result) => { if (active) setStatus(result); })
      .catch(() => { if (active) setNotice("Nexus could not load LinkedIn status. Check that the backend is running, then retry."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const result = new URLSearchParams(window.location.search).get("linkedin");
    if (result) {
      const timer = window.setTimeout(() => setNotice(statusMessage(result)), 0);
      window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
      return () => window.clearTimeout(timer);
    }
  }, []);

  const refresh = async () => {
    setRefreshing(true);
    setNotice(null);
    try {
      setStatus(await api<LinkedInStatus>("/linkedin/sync", { method: "POST" }));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "LinkedIn profile refresh failed.");
    } finally {
      setRefreshing(false);
    }
  };

  const profile = status?.profile;
  const connected = Boolean(status?.connected && profile);
  const initial = (profile?.name || "L").trim().slice(0, 1).toUpperCase();
  const careerTasks = tasks.filter((task) => task.category === "Career");
  const openCareerTasks = careerTasks.filter((task) => task.status !== "completed");
  const doneCareerTasks = careerTasks.filter((task) => task.status === "completed");
  const now = new Date();
  const weekEnd = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
  const overdueCareerTasks = openCareerTasks.filter((task) => task.deadline && new Date(task.deadline) < now);
  const dueSoonCareerTasks = openCareerTasks.filter((task) => task.deadline && new Date(task.deadline) >= now && new Date(task.deadline) <= weekEnd);
  const highPriorityCareerTasks = openCareerTasks.filter((task) => task.priority === "high");
  const classifyCareerTask = (task: typeof careerTasks[number]) => {
    const text = `${task.title} ${task.description}`.toLowerCase();
    if (/interview|screening|technical round|hr round/.test(text)) return "Interviews";
    if (/network|connect|referral|mentor|coffee chat/.test(text)) return "Networking";
    if (/follow[ -]?up|reach[ -]?out|reply|contact|thank you/.test(text)) return "Follow-ups";
    if (/apply|application|resume|curriculum vitae|\bcv\b|job|vacancy|role/.test(text)) return "Applications";
    return "Other career tasks";
  };
  const pipelineBuckets = ["Applications", "Interviews", "Networking", "Follow-ups", "Other career tasks"].map((label) => ({
    label,
    count: openCareerTasks.filter((task) => classifyCareerTask(task) === label).length,
  }));
  const maxPipelineCount = Math.max(1, ...pipelineBuckets.map((item) => item.count));
  const nextFourWeeks = Array.from({ length: 4 }, (_, index) => {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() + index * 7);
    const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7);
    const count = openCareerTasks.filter((task) => {
      if (!task.deadline) return false;
      const deadline = new Date(task.deadline);
      return deadline >= start && deadline < end;
    }).length;
    return { label: index === 0 ? "This week" : `Week ${index + 1}`, count };
  });
  const maxWeekCount = Math.max(1, ...nextFourWeeks.map((week) => week.count));
  const nextCareerTasks = [...openCareerTasks]
    .sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"))
    .slice(0, 4);
  const recommendedCareerTask = [...openCareerTasks].sort((a, b) => {
    const score = (task: typeof careerTasks[number]) => {
      const deadline = task.deadline ? new Date(task.deadline) : null;
      return (deadline && deadline < now ? 100 : 0) + (task.priority === "high" ? 30 : task.priority === "medium" ? 15 : 0) + (deadline && deadline >= now && deadline <= weekEnd ? 20 : 0);
    };
    return score(b) - score(a) || (a.deadline || "9999").localeCompare(b.deadline || "9999");
  })[0];
  const recommendedCareerReason = recommendedCareerTask
    ? recommendedCareerTask.deadline && new Date(recommendedCareerTask.deadline) < now
      ? "This career follow-up is overdue. Review it first, then update its status or deadline."
      : dueSoonCareerTasks.some((task) => task.id === recommendedCareerTask.id)
        ? "This career item is due within the next seven days. Protect time for it before the deadline."
        : recommendedCareerTask.priority === "high"
          ? "This is your highest-priority open career item. Give it a focused work block."
          : "Add a realistic due date so this item stays visible in your plan."
    : doneCareerTasks.length
      ? "Your saved career tasks are complete. Add the next application, interview, or networking step when it comes up."
      : "Save applications, interviews, or networking follow-ups under Career to build a useful pipeline view.";
  const workloadTotal = openCareerTasks.length + doneCareerTasks.length;
  const openShare = workloadTotal ? Math.round((openCareerTasks.length / workloadTotal) * 100) : 0;

  return <AppShell title="LinkedIn" breadcrumb="Nexus AI / Professional hub">
    <div className="mx-auto max-w-[1320px]">
      <header className="relative overflow-hidden rounded-3xl border border-[#0A66C2]/20 bg-gradient-to-br from-[#0A66C2] via-[#07579e] to-[#073b68] p-6 text-white shadow-lg sm:p-8">
        <div className="pointer-events-none absolute -right-16 -top-24 h-72 w-72 rounded-full border border-white/10" />
        <div className="pointer-events-none absolute -right-2 -top-10 h-52 w-52 rounded-full border border-white/10" />
        <div className="relative flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
          <div className="max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1.5 text-[11px] font-semibold tracking-wide"><BriefcaseBusiness size={14} /> PROFESSIONAL HUB</div>
            <h1 className="mt-4 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Your professional world, at a glance.</h1>
            <p className="mt-2 max-w-xl text-sm leading-6 text-blue-50/85">Your profile signals, career workload, and next professional actions—together in one view.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {connected ? <button onClick={() => void refresh()} disabled={refreshing} className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-[#07579e] shadow-sm transition hover:bg-blue-50 disabled:opacity-60"><RefreshCw size={15} className={refreshing ? "animate-spin" : ""} />{refreshing ? "Refreshing…" : "Refresh profile"}</button> : <a href="/api/auth/linkedin/connect" className="inline-flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-[#07579e] shadow-sm transition hover:bg-blue-50">Connect LinkedIn <ArrowRight size={15} /></a>}
            <a href="https://www.linkedin.com/feed/" target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-xl border border-white/25 bg-white/10 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-white/15">Open LinkedIn <ExternalLink size={14} /></a>
          </div>
        </div>
        <div className="relative mt-7 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-white/15 pt-4 text-xs text-blue-50/85">
          <span className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-300" : "bg-amber-300"}`} />{loading ? "Checking connection…" : connected ? "LinkedIn connected" : "Profile not connected"}</span>
          <span className="inline-flex items-center gap-1.5"><Clock3 size={13} />Last refresh: {formatTime(status?.lastSyncedAt ?? null)}</span>
          <span className="inline-flex items-center gap-1.5"><ShieldCheck size={13} />Basic profile snapshot</span>
        </div>
      </header>

      {notice && <div role="status" className="mt-4 rounded-xl border border-high/25 bg-high-bg px-4 py-3 text-sm text-high">{notice}</div>}

      <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Connection" value={loading ? "Checking…" : connected ? "Connected" : "Not connected"} hint={connected ? "Your profile can be refreshed" : "Connect to load your profile"} icon={ShieldCheck} />
        <Metric label="Career completion" value={workloadTotal ? `${Math.round(doneCareerTasks.length / workloadTotal * 100)}%` : "—"} hint={`${doneCareerTasks.length} of ${workloadTotal} saved career steps completed`} icon={Check} />
        <Metric label="Career tasks needing attention" value={connected ? `${overdueCareerTasks.length + dueSoonCareerTasks.length}` : "—"} hint={`${overdueCareerTasks.length} overdue · ${dueSoonCareerTasks.length} due within 7 days`} icon={Target} />
        <Metric label="Last refreshed" value={status?.lastSyncedAt ? new Date(status.lastSyncedAt).toLocaleDateString() : "—"} hint={formatTime(status?.lastSyncedAt ?? null)} icon={Clock3} />
      </section>

      <section className="mt-5">
        <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm sm:p-6">
          <div className="flex items-center justify-between gap-3"><div><div className="flex items-center gap-2"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#0A66C2]/10 text-[#0A66C2]"><BriefcaseBusiness size={17} /></span><h2 className="font-display text-lg font-semibold text-text">Career workload</h2></div><p className="mt-2 text-sm text-text-muted">Career-category tasks already saved in your Nexus workspace.</p></div><a href="/tasks" className="shrink-0 text-xs font-semibold text-primary hover:underline">View tasks <ArrowUpRight size={13} className="inline" /></a></div>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-surface-muted p-4"><p className="text-xs font-medium text-text-muted">Open</p><p className="mt-1 text-3xl font-semibold text-text">{openCareerTasks.length}</p><div className="mt-3 h-2 overflow-hidden rounded-full bg-border"><div className="h-full rounded-full bg-[#0A66C2] transition-all" style={{ width: `${openCareerTasks.length ? Math.max(openShare, 8) : 0}%` }} /></div></div>
            <div className="rounded-xl bg-surface-muted p-4"><p className="text-xs font-medium text-text-muted">Completed</p><p className="mt-1 text-3xl font-semibold text-text">{doneCareerTasks.length}</p><div className="mt-3 h-2 overflow-hidden rounded-full bg-border"><div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${doneCareerTasks.length && workloadTotal ? Math.max(100 - openShare, 8) : 0}%` }} /></div></div>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-lg border border-border px-2 py-2"><p className="text-lg font-semibold text-high">{overdueCareerTasks.length}</p><p className="text-[10px] text-text-muted">Overdue</p></div>
            <div className="rounded-lg border border-border px-2 py-2"><p className="text-lg font-semibold text-amber-600">{dueSoonCareerTasks.length}</p><p className="text-[10px] text-text-muted">Due in 7 days</p></div>
            <div className="rounded-lg border border-border px-2 py-2"><p className="text-lg font-semibold text-[#0A66C2]">{highPriorityCareerTasks.length}</p><p className="text-[10px] text-text-muted">High priority</p></div>
          </div>
          {nextCareerTasks.length ? <div className="mt-4 space-y-2">{nextCareerTasks.map((task) => <a key={task.id} href="/tasks" className="flex items-center gap-3 rounded-xl border border-border px-3.5 py-3 transition hover:border-primary/35 hover:bg-surface-muted"><span className={`h-2 w-2 shrink-0 rounded-full ${task.priority === "high" ? "bg-high" : task.priority === "medium" ? "bg-amber-500" : "bg-primary"}`} /><span className="min-w-0 flex-1 truncate text-sm font-medium text-text">{task.title}</span><span className="shrink-0 text-[11px] text-text-muted">{task.deadlineLabel || "No deadline"}</span></a>)}</div> : <div className="mt-4 rounded-xl border border-dashed border-border px-4 py-5 text-center"><p className="text-sm font-medium text-text">No career tasks saved yet</p><p className="mt-1 text-xs text-text-muted">Save interview, application, or networking follow-ups under Career to track them here.</p></div>}
        </article>
      </section>

      <section className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
        <article className="overflow-hidden rounded-2xl border border-border bg-surface shadow-sm">
          <div className="h-24 bg-gradient-to-r from-[#dbeafe] via-[#e9f3ff] to-[#f3f8ff]" />
          <div className="px-5 pb-5 sm:px-6">
            <div className="-mt-11 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <div className="flex items-end gap-3">
                {profile?.pictureUrl ? <Image src={profile.pictureUrl} alt="" width={76} height={76} unoptimized referrerPolicy="no-referrer" className="h-[76px] w-[76px] rounded-2xl border-4 border-surface bg-surface object-cover shadow-sm" /> : <span className="flex h-[76px] w-[76px] items-center justify-center rounded-2xl border-4 border-surface bg-[#0A66C2] text-2xl font-semibold text-white shadow-sm">{connected ? initial : <UserRound size={27} />}</span>}
                <div className="pb-1"><h2 className="font-display text-xl font-semibold text-text">{profile?.name || (connected ? "LinkedIn member" : "Your LinkedIn profile")}</h2><p className="mt-0.5 max-w-xl text-sm text-text-muted">{profile?.headline || (connected ? "Add a headline on LinkedIn to show it here." : "Connect your account to load your profile details.")}</p></div>
              </div>
              {connected && <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-low-bg px-3 py-1.5 text-xs font-semibold text-low"><Check size={13} /> Synced profile</span>}
            </div>
            <div className="mt-5 grid gap-3 border-t border-border pt-4 sm:grid-cols-2">
              <div><p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">Email</p><p className="mt-1 truncate text-sm text-text">{profile?.email || "Not shared by LinkedIn"}{profile?.emailVerified ? " · verified" : ""}</p></div>
              <div><p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint">Profile language</p><p className="mt-1 text-sm text-text">{profile?.locale || "Not provided"}</p></div>
            </div>
            <p className="mt-4 text-[11px] leading-5 text-text-faint">This is a snapshot of details returned by LinkedIn. Refresh it to load the latest profile fields; Nexus does not edit or publish to your profile.</p>
          </div>
        </article>

        <article className="rounded-2xl border border-border bg-surface p-5 shadow-sm sm:p-6">
          <div className="flex items-start gap-3"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#0A66C2]/10 text-[#0A66C2]"><BarChart3 size={17} /></span><div><h2 className="font-display text-lg font-semibold text-text">Career pipeline</h2><p className="mt-1 text-sm leading-5 text-text-muted">Open Career tasks grouped into practical next-step stages.</p></div></div>
          <div className="mt-5 space-y-3">{pipelineBuckets.map((bucket) => <div key={bucket.label} className="flex items-center gap-3"><span className="w-28 shrink-0 text-xs text-text-muted">{bucket.label}</span><div className="h-2 flex-1 overflow-hidden rounded-full bg-border"><div className="h-full rounded-full bg-[#0A66C2] transition-all" style={{ width: `${bucket.count ? Math.max(8, bucket.count / maxPipelineCount * 100) : 0}%` }} /></div><span className="w-7 text-right text-xs font-semibold text-text">{bucket.count}</span></div>)}</div>
          <div className="mt-5 border-t border-border pt-4"><h3 className="text-sm font-semibold text-text">Deadlines over the next four weeks</h3><div className="mt-3 grid grid-cols-4 gap-2">{nextFourWeeks.map((week) => <div key={week.label} className="rounded-xl bg-surface-muted p-2.5"><div className="flex h-16 items-end"><div className="w-full rounded-t-md bg-[#0A66C2] transition-all" style={{ height: `${week.count ? Math.max(12, week.count / maxWeekCount * 100) : 4}%`, opacity: week.count ? 1 : 0.25 }} /></div><p className="mt-2 truncate text-[10px] font-medium text-text-muted">{week.label}</p><p className="text-xs font-semibold text-text">{week.count} due</p></div>)}</div></div>
          <div className="mt-4 rounded-xl border border-[#0A66C2]/15 bg-[#0A66C2]/[0.04] p-4"><p className="text-[10px] font-semibold uppercase tracking-wide text-[#0A66C2]">Suggested next move</p>{recommendedCareerTask ? <><p className="mt-1 text-sm font-semibold text-text">{recommendedCareerTask.title}</p><p className="mt-1 text-xs leading-5 text-text-muted">{recommendedCareerReason}</p><a href={`/tasks#task-${recommendedCareerTask.id}`} className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">Open this task <ArrowUpRight size={13} /></a></> : <><p className="mt-1 text-sm font-medium text-text">Build your career pipeline</p><p className="mt-1 text-xs leading-5 text-text-muted">{recommendedCareerReason}</p><a href="/tasks" className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline">Add a career task <ArrowUpRight size={13} /></a></>}</div>
          <p className="mt-3 text-[10px] leading-4 text-text-faint">These insights use your LinkedIn profile snapshot and Nexus Career tasks. Pipeline stages are inferred from task wording; LinkedIn feed and connection data are not part of the current access.</p>
        </article>
      </section>

      <section className="mt-5 grid gap-4 md:grid-cols-2">
        <article className="rounded-2xl border border-border bg-surface p-5">
          <div className="flex items-center gap-2"><Sparkles size={16} className="text-primary" /><h2 className="text-sm font-semibold text-text">Professional workspace</h2></div>
          <p className="mt-2 text-xs leading-5 text-text-muted">Use this page as the home for your professional profile. Refresh the snapshot here, and review career-related tasks separately in Nexus.</p>
          <a href="/tasks" className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-primary">Review career tasks <ArrowRight size={13} /></a>
        </article>
        <article className="rounded-2xl border border-[#0A66C2]/15 bg-[#0A66C2]/[0.035] p-5">
          <div className="flex items-center gap-2"><ShieldCheck size={16} className="text-[#0A66C2]" /><h2 className="text-sm font-semibold text-text">Private by design</h2></div>
          <p className="mt-2 text-xs leading-5 text-text-muted">Nexus stores only the profile claims LinkedIn returns, encrypts the access token, and keeps this connection tied to your signed-in workspace account.</p>
          <a href="/sources" className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-primary">Manage connected services <ArrowRight size={13} /></a>
        </article>
      </section>
    </div>
  </AppShell>;
}
