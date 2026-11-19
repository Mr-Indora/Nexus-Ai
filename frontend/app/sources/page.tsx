"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import AppShell from "@/components/AppShell";
import SourceCard from "@/components/SourceCard";
import { api } from "@/lib/api";
import { useNexus } from "@/lib/store";
import { GraduationCap, Network, RefreshCw, ShieldCheck, Unplug } from "lucide-react";

interface PidsConnection {
  kind: string;
  displayName: string;
  status: string;
  sync: { status: string; lastAttemptAt: string | null; lastSuccessAt: string | null; errorCode: string | null } | null;
}

interface AcademicUpdate {
  id: string;
  title: string;
  summary: string;
  topic?: string;
  analysisMethod?: string;
  url: string | null;
  publishedAt: string | null;
  checkedAt: string | null;
  course?: string | null;
  author?: string | null;
  kind?: string;
  read?: boolean;
}

interface AcademicOverview {
  sources: PidsConnection[];
  collegeNotices: { items: AcademicUpdate[]; lastCheckedAt: string | null; syncStatus: string; newNoticeCount: number };
  classroomUpdates: { items: AcademicUpdate[]; lastCheckedAt: string | null; syncStatus: string };
}

interface CollegeNoticeList {
  items: AcademicUpdate[];
  unreadCount: number;
  lastCheckedAt: string | null;
  lastAttemptAt: string | null;
  syncStatus: string;
}

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
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Time unavailable" : date.toLocaleString();
}

function safeExternalUrl(value: string | null) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

function classroomErrorMessage(code: string | null) {
  if (code === "classroom_permission_required") return "Google did not grant Classroom read permission. Refresh Google permissions, then sync again.";
  if (code === "classroom_api_not_enabled") return "The Classroom API is disabled in the Google Cloud project. Enable it, then reconnect Google.";
  if (code === "classroom_access_denied") return "Google blocked a Classroom read. Confirm this account is enrolled in an active course, then reconnect Google permissions and sync again.";
  if (code === "classroom_source_unavailable") return "Classroom could not be reached during the latest sync. Try Sync now again.";
  return code ? "The latest Classroom sync did not finish. Try Sync now again." : null;
}

function linkedinMessage(code: string | null) {
  if (!code) return null;
  const messages: Record<string, string> = {
    database_update_required: "Nexus needs its database update before LinkedIn can be connected.",
    not_configured: "LinkedIn app credentials have not been added to the backend yet.",
    linkedin_not_configured: "LinkedIn app credentials have not been added to the backend yet.",
    real_account_required: "Sign in with your Google account before connecting LinkedIn.",
    invalid_state: "This connection request expired or could not be verified. Try connecting again.",
    authorization_cancelled: "LinkedIn access was not approved. You can connect again whenever you are ready.",
    authorization_failed: "LinkedIn could not complete sign-in. Please try again.",
    linkedin_id_token_invalid: "LinkedIn sign-in finished, but Nexus could not verify the profile response. Please connect again.",
    linkedin_app_mismatch: "The LinkedIn app ID in Nexus does not match the app that issued this sign-in. Check the LinkedIn Client ID in backend/.env.",
    linkedin_id_token_expired: "LinkedIn sign-in expired before Nexus could finish. Please connect again.",
    linkedin_signing_key_unavailable: "Nexus could not reach LinkedIn to verify the sign-in. Please try again shortly.",
    linkedin_id_token_signature_invalid: "LinkedIn returned a profile token that Nexus could not verify. Recheck the LinkedIn app credentials and try again.",
    linkedin_id_token_claims_missing: "LinkedIn returned an incomplete sign-in response. Confirm OpenID Connect is enabled for this app, then try again.",
    linkedin_connect_unavailable: "Nexus could not start the LinkedIn connection just now. Please wait a moment and try again.",
    linkedin_reconnect_required: "LinkedIn needs you to reconnect before Nexus can sync your profile.",
    linkedin_permission_denied: "LinkedIn did not allow Nexus to read the approved profile details.",
    linkedin_rate_limited: "LinkedIn is receiving too many requests. Try again in a little while.",
    linkedin_temporarily_unavailable: "LinkedIn is temporarily unavailable. Try syncing again shortly.",
    linkedin_sync_failed: "LinkedIn profile sync did not finish. Please try again.",
  };
  return messages[code] ?? "LinkedIn could not be updated. Reconnect and try again.";
}

function UpdateList({ items, emptyLabel, onOpen }: { items: AcademicUpdate[]; emptyLabel: string; onOpen?: (item: AcademicUpdate) => void }) {
  const itemNodes = useRef(new Map<string, HTMLLIElement>());
  useEffect(() => {
    if (!onOpen || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting || entry.intersectionRatio < 0.55) continue;
        const item = items.find((row) => row.id === (entry.target as HTMLElement).dataset.noticeId);
        if (item && item.read === false) onOpen(item);
      }
    }, { threshold: [0.55] });
    for (const item of items) {
      const node = itemNodes.current.get(item.id);
      if (node && item.read === false) observer.observe(node);
    }
    return () => observer.disconnect();
  }, [items, onOpen]);
  if (!items.length) return <p className="mt-4 rounded-lg bg-surface-muted px-3 py-3 text-xs text-text-muted">{emptyLabel}</p>;
  return <ul className="mt-4 divide-y divide-border">
    {items.map((item) => <li key={item.id} data-notice-id={item.id} ref={(node) => { if (node) itemNodes.current.set(item.id, node); else itemNodes.current.delete(item.id); }} className="py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">{safeExternalUrl(item.url) ? <a href={safeExternalUrl(item.url)} target="_blank" rel="noreferrer" onClick={() => onOpen?.(item)} className="font-medium text-sm text-primary underline underline-offset-2">{item.title}</a> : item.read === false && onOpen ? <button type="button" onClick={() => onOpen(item)} className="text-left font-medium text-sm text-primary underline underline-offset-2">{item.title}</button> : <p className="font-medium text-sm text-text">{item.title}</p>}{item.topic && <span className="rounded-full bg-primary-dim px-2 py-0.5 text-[9px] font-medium text-primary">{item.topic}</span>}{item.read === false && <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[9px] font-semibold text-primary">Unread</span>}</div>
      <p className="mt-1 line-clamp-2 text-xs leading-5 text-text-muted">{item.summary || "Open the original update for details."}</p>
      <p className="mt-1 text-[11px] text-text-faint">{[item.analysisMethod === "gemini_academic_summary_v1" ? "Gemini summary" : "Source text preview", item.course, item.author ? `By ${item.author}` : null, `${item.kind?.startsWith("classroom_") ? "Updated" : "Notice date"}: ${formatTime(item.publishedAt || item.checkedAt)}`].filter(Boolean).join(" · ")}</p>
    </li>)}
  </ul>;
}

export default function SourcesPage() {
  const { sources, user, sync, syncing } = useNexus();
  const [academicOverview, setAcademicOverview] = useState<AcademicOverview | null>(null);
  const [collegeNoticeList, setCollegeNoticeList] = useState<CollegeNoticeList | null>(null);
  const [showAllCollegeNotices, setShowAllCollegeNotices] = useState(false);
  const [linkedin, setLinkedin] = useState<LinkedInStatus | null>(null);
  const [linkedinBusy, setLinkedinBusy] = useState(false);
  const [linkedinNotice, setLinkedinNotice] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api<AcademicOverview>("/pids/overview")
      .then((overview) => { if (active) setAcademicOverview(overview); })
      .catch(() => { if (active) setAcademicOverview(null); });
    api<CollegeNoticeList>("/pids/college-notices?limit=1000")
      .then((result) => { if (active) setCollegeNoticeList(result); })
      .catch(() => { if (active) setCollegeNoticeList(null); });
    return () => { active = false; };
  }, [syncing]);

  useEffect(() => {
    let active = true;
    api<LinkedInStatus>("/linkedin/status")
      .then((status) => { if (active) setLinkedin(status); })
      .catch(() => { if (active) setLinkedin(null); });
    const query = new URLSearchParams(window.location.search);
    const result = query.get("linkedin");
    let noticeTimer: number | undefined;
    if (result) {
      noticeTimer = window.setTimeout(() => {
        if (active) setLinkedinNotice(linkedinMessage(result));
      }, 0);
    }
    if (result) window.history.replaceState({}, "", `${window.location.pathname}${window.location.hash}`);
    return () => { active = false; if (noticeTimer !== undefined) window.clearTimeout(noticeTimer); };
  }, []);

  const syncLinkedIn = async () => {
    setLinkedinBusy(true);
    setLinkedinNotice(null);
    try {
      setLinkedin(await api<LinkedInStatus>("/linkedin/sync", { method: "POST" }));
    } catch (error) {
      setLinkedinNotice(error instanceof Error ? error.message : "LinkedIn sync failed. Try again.");
    } finally {
      setLinkedinBusy(false);
    }
  };

  const disconnectLinkedIn = async () => {
    if (!window.confirm("Disconnect LinkedIn and remove its saved profile and tokens from this Nexus account?")) return;
    setLinkedinBusy(true);
    setLinkedinNotice(null);
    try {
      await api("/linkedin/connection", { method: "DELETE" });
      setLinkedin(await api<LinkedInStatus>("/linkedin/status"));
    } catch (error) {
      setLinkedinNotice(error instanceof Error ? error.message : "Could not disconnect LinkedIn.");
    } finally {
      setLinkedinBusy(false);
    }
  };

  const connectionFor = (kind: string) => academicOverview?.sources.find((item) => item.kind === kind);
  // Always keep Gmail and Calendar visible, even if the sources request is
  // temporarily empty while the workspace is loading. A signed-in Google
  // session is required by Nexus and grants both baseline integrations.
  const googleSources = ([
    {
      id: "gmail", name: "Gmail", type: "gmail" as const,
      description: "Reads recent inbox emails (read-only) and extracts useful actions.",
      lastSynced: user?.gmailLastSyncedAt ?? null,
      itemCount: 0,
    },
    {
      id: "calendar", name: "Google Calendar", type: "calendar" as const,
      description: "Reads your events and can update events you choose.",
      lastSynced: user?.calendarLastSyncedAt ?? null,
      itemCount: 0,
    },
  ]).map((fallback) => sources.find((source) => source.type === fallback.type) ?? {
    ...fallback,
    status: user ? "connected" as const : "disconnected" as const,
  });
  const classroom = connectionFor("google_classroom");
  const collegeItems = collegeNoticeList?.items ?? academicOverview?.collegeNotices.items ?? [];
  const classroomItems = academicOverview?.classroomUpdates.items ?? [];
  const unreadCollegeItems = collegeItems.filter((item) => !item.read);
  const orderedCollegeItems = [...unreadCollegeItems, ...collegeItems.filter((item) => item.read)];
  const visibleCollegeItems = showAllCollegeNotices
    ? orderedCollegeItems
    : orderedCollegeItems.slice(0, 10);
  const hiddenCollegeNoticeCount = Math.max(0, orderedCollegeItems.length - visibleCollegeItems.length);
  const markCollegeRead = async (item: AcademicUpdate) => {
    if (item.read) return;
    setCollegeNoticeList((current) => current ? {
      ...current,
      unreadCount: Math.max(0, current.unreadCount - 1),
      items: current.items.map((entry) => entry.id === item.id ? { ...entry, read: true } : entry),
    } : current);
    try {
      await api(`/pids/college-notices/${encodeURIComponent(item.id)}/read`, { method: "POST" });
      window.localStorage.setItem("nexus-college-notices-read-change", `${item.id}:${Date.now()}`);
      window.dispatchEvent(new Event("nexus-college-notices-read"));
    } catch {
      api<CollegeNoticeList>("/pids/college-notices?limit=1000").then(setCollegeNoticeList).catch(() => {});
    }
  };

  return (
    <AppShell title="Connections" breadcrumb="Nexus AI / Connections">
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="font-display text-xl font-semibold text-text">Your connections</h2>
            <p className="mt-1 text-[13.5px] text-text-muted">
            See which services Nexus can read from or update and when they last synced.
          </p>
        </div>
        <button onClick={() => void sync()} disabled={syncing} className="flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-[13px] font-semibold text-white hover:bg-primary-hover disabled:opacity-60">
          <RefreshCw size={14} className={syncing ? "animate-spin" : ""} /> {syncing ? "Syncing…" : "Sync now"}
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {googleSources.map((s) => (
          <SourceCard key={s.id} source={s} />
        ))}
        <article className="rounded-xl border border-border bg-surface p-5 shadow-sm">
          <div className="flex items-start justify-between"><div className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-600"><GraduationCap size={21} /></div><span className={`rounded-md px-2 py-1 text-[11px] font-semibold ${classroom?.sync?.lastSuccessAt ? "bg-low-bg text-low" : classroom?.sync?.lastAttemptAt ? "bg-high-bg text-high" : "bg-surface-muted text-text-faint"}`}>{classroom?.sync?.lastSuccessAt ? "Connected" : classroom?.sync?.lastAttemptAt ? "Needs attention" : "Not checked"}</span></div>
          <h3 className="mt-4 text-[15px] font-semibold text-text">Google Classroom</h3>
          <p className="mt-1 text-[12.5px] leading-relaxed text-text-muted">Reads active courses, assignments and announcements with read-only access.</p>
          <div className="mt-4 flex items-center justify-between text-[11.5px] text-text-faint"><span>{classroomItems.length} recent updates</span><span>{classroom?.sync?.lastSuccessAt ? `Synced ${formatTime(classroom.sync.lastSuccessAt)}` : "Sync to check access"}</span></div>
        </article>
      </div>

      <section className="mt-7">
        <h2 className="font-display text-lg font-semibold text-text">Professional profile</h2>
        <p className="mt-1 text-[13px] text-text-muted">Connect your LinkedIn profile to keep your basic professional identity available in Nexus.</p>
        <article className="mt-3 max-w-3xl rounded-xl border border-border bg-surface p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex min-w-0 items-start gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#0A66C2]/10 text-[#0A66C2]"><Network size={23} /></span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><h3 className="text-[15px] font-semibold text-text">LinkedIn</h3><span className={`rounded-md px-2 py-1 text-[11px] font-semibold ${linkedin?.connected ? "bg-low-bg text-low" : "bg-surface-muted text-text-faint"}`}>{linkedin?.connected ? "Connected" : linkedin?.status === "database_update_required" ? "Setup needed" : linkedin?.configured ? "Disconnected" : "Needs app setup"}</span></div>
                <p className="mt-1 text-[12.5px] leading-relaxed text-text-muted">Reads the authorized profile snapshot. Network activity needs additional LinkedIn approval.</p>
              </div>
            </div>
            {linkedin?.connected ? <div className="flex shrink-0 gap-2">
              <button onClick={() => void syncLinkedIn()} disabled={linkedinBusy} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-60"><RefreshCw size={13} className={linkedinBusy ? "animate-spin" : ""} />Sync now</button>
              {linkedin.status === "reconnect_required" && <a href="/api/auth/linkedin/connect" className="rounded-lg border border-border px-3 py-2 text-xs font-semibold text-text hover:bg-surface-muted">Reconnect</a>}
              <button onClick={() => void disconnectLinkedIn()} disabled={linkedinBusy} aria-label="Disconnect LinkedIn" className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs text-text-muted hover:bg-surface-muted disabled:opacity-60"><Unplug size={13} />Disconnect</button>
            </div> : <a href="/api/auth/linkedin/connect" aria-disabled={!linkedin?.configured || linkedin?.status === "database_update_required"} onClick={(event) => { if (!linkedin?.configured || linkedin.status === "database_update_required") event.preventDefault(); }} className={`rounded-lg px-3 py-2 text-xs font-semibold text-white ${linkedin?.configured && linkedin.status !== "database_update_required" ? "bg-[#0A66C2] hover:bg-[#004182]" : "pointer-events-none bg-text-faint"}`}>Connect LinkedIn</a>}
          </div>
          {linkedin?.profile && <div className="mt-4 flex items-center gap-3 rounded-lg bg-surface-muted/50 p-3">
            {linkedin.profile.pictureUrl ? <Image src={linkedin.profile.pictureUrl} alt="" width={44} height={44} unoptimized referrerPolicy="no-referrer" className="h-11 w-11 rounded-full object-cover" /> : <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[#0A66C2] text-sm font-semibold text-white">{(linkedin.profile.name || "L").slice(0, 1).toUpperCase()}</span>}
            <div className="min-w-0"><p className="truncate text-sm font-semibold text-text">{linkedin.profile.name || "LinkedIn member"}</p><p className="truncate text-xs text-text-muted">{linkedin.profile.headline || linkedin.profile.email || "Profile connected"}</p></div>
            <ShieldCheck className="ml-auto shrink-0 text-low" size={17} />
          </div>}
          <p className="mt-3 text-[11.5px] text-text-faint">{linkedin?.lastSyncedAt ? `Profile last synced ${formatTime(linkedin.lastSyncedAt)}` : linkedin?.lastAttemptAt ? `Last attempt ${formatTime(linkedin.lastAttemptAt)}` : "No profile has been synced yet."}</p>
          {(linkedinNotice || linkedin?.lastErrorCode) && <p role="status" className="mt-2 rounded-lg border border-high/30 bg-high-bg px-3 py-2 text-xs text-high">{linkedinNotice || linkedinMessage(linkedin?.lastErrorCode ?? null)}</p>}
          <a href="/linkedin" className="mt-3 inline-flex text-xs font-semibold text-primary hover:underline">Open professional hub →</a>
        </article>
      </section>

      <section className="mt-7">
        <h2 className="font-display text-lg font-semibold text-text">Study sources</h2>
        <p className="mt-1 text-[13px] text-text-muted">While this workspace is open, Nexus checks connected sources every 30 seconds. Use Sync now to check immediately.</p>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <article id="college-notices" className="scroll-mt-6 rounded-xl border border-border bg-surface p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div><h3 className="text-[15px] font-semibold text-text">Engineering College Ajmer notices</h3><p className="mt-1 text-[12.5px] text-text-muted">Reads the college&apos;s public circular list and follows each notice&apos;s original link.</p></div>
              <span className="rounded-md bg-low-bg px-2 py-1 text-[11px] font-semibold text-low">Public source</span>
            </div>
            <p className="mt-3 text-[11.5px] text-text-faint">{collegeNoticeList?.lastCheckedAt ? `Last checked ${formatTime(collegeNoticeList.lastCheckedAt)}` : "Not checked yet — Nexus checks automatically while this workspace stays open."}</p>
            {academicOverview?.collegeNotices.syncStatus === "error" ? <p className="mt-1 text-xs font-medium text-text-muted">The latest site check failed. Use Sync now to retry.</p> : academicOverview?.collegeNotices.lastCheckedAt && <p className="mt-1 text-xs font-medium text-text-muted">{academicOverview.collegeNotices.newNoticeCount ? `${academicOverview.collegeNotices.newNoticeCount} new notice${academicOverview.collegeNotices.newNoticeCount === 1 ? "" : "s"} found in the last check.` : "No new notice was found in the last check."}</p>}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2"><p className="text-[11px] font-medium text-text-muted">{collegeNoticeList?.unreadCount ?? unreadCollegeItems.length} unread · showing {visibleCollegeItems.length} of {collegeItems.length}</p>{(collegeNoticeList?.unreadCount ?? 0) > 0 && <span className="rounded-full bg-primary/10 px-2.5 py-1 text-[10px] font-semibold text-primary">{collegeNoticeList?.unreadCount} to read</span>}</div>
            <UpdateList items={visibleCollegeItems} onOpen={(item) => void markCollegeRead(item)} emptyLabel="No college notices have been saved yet. Nexus will check again in the background while this workspace is open." />
            {hiddenCollegeNoticeCount > 0 && <button type="button" onClick={() => setShowAllCollegeNotices(true)} className="mt-3 rounded-lg border border-border px-3 py-2 text-xs font-semibold text-primary hover:bg-surface-muted">Show {hiddenCollegeNoticeCount} more notice{hiddenCollegeNoticeCount === 1 ? "" : "s"}</button>}
            {showAllCollegeNotices && orderedCollegeItems.length > 10 && <button type="button" onClick={() => setShowAllCollegeNotices(false)} className="ml-2 mt-3 rounded-lg px-3 py-2 text-xs font-semibold text-text-muted hover:bg-surface-muted">Show fewer</button>}
            <a href="https://www.ecajmer.ac.in/allcirculars" target="_blank" rel="noreferrer" className="mt-3 inline-flex text-xs font-medium text-primary underline underline-offset-2">Open college circulars</a>
          </article>
          <article className="rounded-xl border border-border bg-surface p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div><h3 className="text-[15px] font-semibold text-text">Google Classroom</h3><p className="mt-1 text-[12.5px] text-text-muted">Reads active courses, published assignments, study materials and announcements. It never submits work or changes grades.</p></div>
              <span className={`rounded-md px-2 py-1 text-[11px] font-semibold ${classroom?.sync?.lastSuccessAt ? "bg-low-bg text-low" : "bg-surface-muted text-text-faint"}`}>{classroom?.sync?.lastSuccessAt ? "Read access confirmed" : classroom?.sync?.lastAttemptAt ? "Sync needs attention" : "Not checked yet"}</span>
            </div>
            <p className="mt-3 text-[11.5px] text-text-faint">{academicOverview?.classroomUpdates.lastCheckedAt ? `Last checked ${formatTime(academicOverview.classroomUpdates.lastCheckedAt)}` : classroom?.sync?.lastAttemptAt ? `Last attempt ${formatTime(classroom.sync.lastAttemptAt)}` : "No successful Classroom read yet. Sync now to check your permission."}</p>
            {!classroom?.sync?.lastSuccessAt && classroom?.sync?.errorCode && <p className="mt-1 text-xs font-medium text-text-muted">{classroomErrorMessage(classroom.sync.errorCode)}</p>}
            <UpdateList items={classroomItems} emptyLabel={academicOverview?.classroomUpdates.lastCheckedAt ? "Classroom was checked successfully; no published classwork or announcements were found." : "No Classroom updates are available yet. Check the permission, then run Sync now."} />
            {!classroom?.sync?.lastSuccessAt && classroom?.sync?.errorCode === "classroom_api_not_enabled" && <a href="https://console.cloud.google.com/apis/library/classroom.googleapis.com" target="_blank" rel="noreferrer" className="mt-3 inline-flex text-xs font-medium text-primary underline underline-offset-2">Open Google Cloud to enable Classroom API</a>}
            {!classroom?.sync?.lastSuccessAt && classroom?.sync?.errorCode !== "classroom_api_not_enabled" && <a href="/api/auth/google/login" className="mt-3 inline-flex text-xs font-medium text-primary underline underline-offset-2">Refresh Google permissions</a>}
          </article>
        </div>
      </section>

      <p className="mt-6 text-[12px] text-text-faint">
        Gmail, college notices, and Classroom are read-only sources. Calendar events can be edited in Nexus and synced to Google after you authorize write access.
      </p>
    </AppShell>
  );
}
