"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, Calendar, Star, Info, Plug } from "lucide-react";
import { useNexus } from "@/lib/store";
import { api } from "@/lib/api";
import { AppNotification, NotificationPreferences } from "@/types";
import { NOTIFICATION_PREFERENCES_UPDATED, readLocalNotificationPreferences, zonedParts } from "@/lib/notification-preferences";

const iconFor: Record<AppNotification["type"], typeof Bell> = {
  deadline: Calendar,
  priority: Star,
  system: Info,
  source: Plug,
  daily_brief: Calendar,
};

interface StoredNotification {
  id: string;
  kind: string;
  title: string;
  body: string;
  createdAt: string;
  readAt: string | null;
}

export default function NotificationPanel() {
  const [open, setOpen] = useState(false);
  const { tasks, events, user } = useNexus();
  const [now, setNow] = useState(() => Date.now());
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const [readOwnerId, setReadOwnerId] = useState<number | null>(null);
  const [academicNotices, setAcademicNotices] = useState<{ id: string; title: string; description: string; href?: string; sourceKind?: string; createdAt?: string }[]>([]);
  const [academicBriefSummary, setAcademicBriefSummary] = useState("");
  const [academicOverviewOwnerId, setAcademicOverviewOwnerId] = useState<number | null>(null);
  const [storedNotifications, setStoredNotifications] = useState<StoredNotification[]>([]);
  const [localDailyBrief, setLocalDailyBrief] = useState<StoredNotification | null>(null);
  const localBriefKey = useRef("");
  const [preferenceStorage, setPreferenceStorage] = useState<"device" | "account">("device");
  const [notificationPreferences, setNotificationPreferences] = useState<NotificationPreferences>({
    dailyBriefEnabled: false, dailyBriefTime: "06:00", timezone: "Asia/Kolkata",
    deadlineReminders: true, highPriorityAlerts: true,
  });
  useEffect(() => {
    if (!user?.id) return;
    let active = true;
    const localPreferences = readLocalNotificationPreferences(user.id);
    queueMicrotask(() => { if (active) setNotificationPreferences(localPreferences); });
    let poll: ReturnType<typeof setInterval> | undefined;
    Promise.all([
      api<StoredNotification[]>("/pids/notifications"),
      api<NotificationPreferences>("/pids/notifications/preferences"),
    ]).then(([items, preferences]) => {
      if (!active) return;
      setStoredNotifications(items);
      setNotificationPreferences(preferences);
      setPreferenceStorage("account");
      poll = setInterval(() => {
        api<StoredNotification[]>("/pids/notifications").then((next) => { if (active) setStoredNotifications(next); }).catch(() => {});
      }, 60_000);
    }).catch(() => {
      if (active) {
        setStoredNotifications([]);
        setPreferenceStorage("device");
        setNotificationPreferences(readLocalNotificationPreferences(user.id));
      }
    });
    const refreshLocalPreferences = () => setNotificationPreferences(readLocalNotificationPreferences(user.id));
    window.addEventListener(NOTIFICATION_PREFERENCES_UPDATED, refreshLocalPreferences);
    return () => { active = false; if (poll) clearInterval(poll); window.removeEventListener(NOTIFICATION_PREFERENCES_UPDATED, refreshLocalPreferences); };
  }, [user?.id]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!user?.id || academicOverviewOwnerId !== user.id || preferenceStorage !== "device" || !notificationPreferences.dailyBriefEnabled) return;
    const { day, minutes } = zonedParts(new Date(now), notificationPreferences.timezone);
    const [hour, minute] = notificationPreferences.dailyBriefTime.split(":").map(Number);
    if (minutes < hour * 60 + minute) return;
    const key = `nexus-local-daily-brief-${user.id}-${day}`;
    try {
      const existing = window.localStorage.getItem(key);
      const openTasks = tasks.filter((task) => task.status !== "completed");
      const dueToday = openTasks.filter((task) => task.deadline && zonedParts(new Date(task.deadline), notificationPreferences.timezone).day === day);
      const overdue = openTasks.filter((task) => task.deadline && new Date(task.deadline).getTime() < now && zonedParts(new Date(task.deadline), notificationPreferences.timezone).day !== day);
      const todayEvents = events.filter((event) => zonedParts(new Date(event.start), notificationPreferences.timezone).day === day);
      const deadlines = dueToday.filter((task) => task.deadline).sort((a, b) => +new Date(a.deadline!) - +new Date(b.deadline!));
      const firstDeadline = deadlines[0];
      const baseBody = `${openTasks.length} open task${openTasks.length === 1 ? "" : "s"}; ${dueToday.length} due today; ${overdue.length} overdue; ${todayEvents.length} calendar event${todayEvents.length === 1 ? "" : "s"}.${firstDeadline ? ` First deadline: ${firstDeadline.title}.` : ""}`;
      const body = [baseBody, academicBriefSummary].filter(Boolean).join(" ");
      if (existing) {
        const prior = JSON.parse(existing) as StoredNotification;
        if (prior.body !== body) {
          const updated = { ...prior, body };
          window.localStorage.setItem(key, JSON.stringify(updated));
          queueMicrotask(() => setLocalDailyBrief(updated));
        } else {
          queueMicrotask(() => setLocalDailyBrief(prior));
        }
        localBriefKey.current = key;
        return;
      }
      if (localBriefKey.current === key) return;
      const brief: StoredNotification = { id: key, kind: "daily_brief", title: "Your daily brief", body, createdAt: new Date(now).toISOString(), readAt: null };
      window.localStorage.setItem(key, JSON.stringify(brief));
      localBriefKey.current = key;
      queueMicrotask(() => setLocalDailyBrief(brief));
      if (typeof Notification !== "undefined" && Notification.permission === "granted") new Notification(brief.title, { body: brief.body });
    } catch { /* Browser storage or notification permission may be unavailable. */ }
  }, [academicBriefSummary, academicOverviewOwnerId, events, now, notificationPreferences, preferenceStorage, tasks, user?.id]);
  useEffect(() => {
    if (!user?.id) {
      let active = true;
      queueMicrotask(() => { if (active) { setAcademicNotices([]); setAcademicBriefSummary(""); setAcademicOverviewOwnerId(null); } });
      return () => { active = false; };
    }
    let active = true;
    const refreshAcademicNotices = () => {
      api<{
        recommendations: { id: string; title: string; description: string; evidence: { url?: string; source_kind?: string }; }[];
        collegeNotices: { newNoticeCount: number; newItems: { title: string }[]; lastCheckedAt: string | null; syncStatus: string; errorCode: string | null };
        classroomUpdates: { newUpdateCount: number; newItems: { title: string }[]; lastCheckedAt: string | null; syncStatus: string; errorCode: string | null };
      }>("/pids/overview")
        .then((overview) => {
          if (!active) return;
          setAcademicNotices(overview.recommendations
            .filter((item) => item.evidence.source_kind === "college_notices" || item.evidence.source_kind === "google_classroom")
            .slice(0, 8)
            .map((item) => ({ id: item.id, title: item.title, description: item.description, href: item.evidence.url, sourceKind: item.evidence.source_kind })));
          const sourceLine = (label: string, count: number, titles: string[], lastCheckedAt: string | null, status: string, errorCode: string | null) => {
            if (errorCode || status === "error" || status === "partial") return `${label}: latest check needs a retry.`;
            if (!lastCheckedAt) return status === "not_connected" ? `${label}: not connected yet.` : `${label}: no successful check yet.`;
            if (!count) return `${label}: no new items in the latest successful check.`;
            const titleText = titles.slice(0, 2).join("; ");
            return `${label}: ${count} new item${count === 1 ? "" : "s"}${titleText ? ` — ${titleText}` : ""}.`;
          };
          setAcademicBriefSummary([
            sourceLine("College notices", overview.collegeNotices.newNoticeCount, overview.collegeNotices.newItems.map((item) => item.title), overview.collegeNotices.lastCheckedAt, overview.collegeNotices.syncStatus, overview.collegeNotices.errorCode),
            sourceLine("Classroom", overview.classroomUpdates.newUpdateCount, overview.classroomUpdates.newItems.map((item) => item.title), overview.classroomUpdates.lastCheckedAt, overview.classroomUpdates.syncStatus, overview.classroomUpdates.errorCode),
          ].join(" "));
          setAcademicOverviewOwnerId(user.id);
        })
        .catch(() => { if (active) { setAcademicNotices([]); setAcademicBriefSummary("Study-source status could not be loaded."); setAcademicOverviewOwnerId(user.id); } });
    };
    refreshAcademicNotices();
    window.addEventListener("nexus-sync-complete", refreshAcademicNotices);
    return () => { active = false; window.removeEventListener("nexus-sync-complete", refreshAcademicNotices); };
  }, [user?.id]);
  useEffect(() => {
    if (!user?.id) return;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      try {
        const stored = window.localStorage.getItem(`nexus-read-notifications-${user.id}`);
        setReadIds(new Set(stored ? JSON.parse(stored) as string[] : []));
      } catch {
        setReadIds(new Set());
      }
      setReadOwnerId(user.id);
    });
    return () => { active = false; };
  }, [user?.id]);
  useEffect(() => {
    if (!user?.id || readOwnerId !== user.id) return;
    window.localStorage.setItem(`nexus-read-notifications-${user.id}`, JSON.stringify([...readIds]));
  }, [readIds, readOwnerId, user?.id]);
  const taskItems: AppNotification[] = tasks
    .filter((t) => t.status !== "completed" && t.deadline)
    .filter((t) => (notificationPreferences.highPriorityAlerts && t.priority === "high") ||
      (notificationPreferences.deadlineReminders && new Date(t.deadline).getTime() - now < 48 * 3600 * 1000))
    .sort((a, b) => +new Date(a.deadline) - +new Date(b.deadline))
    .slice(0, 8)
    .map((t) => ({
      id: t.id,
      title: new Date(t.deadline) < new Date() ? "Overdue task" : t.priority === "high" ? "High-priority deadline" : "Deadline approaching",
      description: t.title,
      time: t.deadlineLabel,
      read: readIds.has(t.id),
      type: "deadline" as const,
    }));
  const noticeItems: AppNotification[] = academicNotices.map((notice) => ({
    id: `academic-${notice.id}`,
    title: notice.title,
    description: notice.description,
    time: notice.sourceKind === "college_notices" ? "College notice" : "Google Classroom",
    read: readIds.has(`academic-${notice.id}`),
    type: "source",
    href: notice.href,
  }));
  const storedItems: AppNotification[] = storedNotifications.map((notification) => ({
    id: `stored-${notification.id}`,
    title: notification.title,
    description: notification.body,
    time: new Date(notification.createdAt).toLocaleString(),
    read: Boolean(notification.readAt),
    type: notification.kind === "daily_brief" ? "daily_brief" : "system",
  }));
  const localBriefItem: AppNotification[] = localDailyBrief ? [{
    id: localDailyBrief.id,
    title: localDailyBrief.title,
    description: localDailyBrief.body,
    time: new Date(localDailyBrief.createdAt).toLocaleString(),
    read: readIds.has(localDailyBrief.id),
    type: "daily_brief",
  }] : [];
  const items = [...storedItems, ...localBriefItem, ...noticeItems, ...taskItems].slice(0, 12);
  const ref = useRef<HTMLDivElement>(null);
  const unread = items.filter((n) => !n.read).length;

  const markAllRead = () => {
    setReadIds(new Set(items.map((n) => n.id)));
    const persisted = storedNotifications.filter((item) => !item.readAt);
    void Promise.all(persisted.map((item) => api(`/pids/notifications/${encodeURIComponent(item.id)}/read`, { method: "POST" })))
      .then(() => setStoredNotifications((current) => current.map((item) => ({ ...item, readAt: item.readAt || new Date().toISOString() }))))
      .catch(() => {});
  };

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="relative rounded-lg p-2 text-text-muted hover:bg-surface-muted hover:text-text"
        aria-label="Notifications"
      >
        <Bell size={19} strokeWidth={1.9} />
        {unread > 0 && (
          <span className="absolute right-1.5 top-1.5 flex h-2 w-2 rounded-full bg-high ring-2 ring-surface" />
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+8px)] z-30 w-80 overflow-hidden rounded-xl border border-border bg-surface shadow-lg">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <span className="text-sm font-semibold text-text">Notifications</span>
            <button
              onClick={markAllRead}
              className="text-xs font-medium text-primary hover:underline"
            >
              Mark all read
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {items.length === 0 && <div className="px-4 py-6 text-center text-[13px] text-text-muted">You&apos;re all caught up.</div>}
            {items.map((n) => {
              const Icon = iconFor[n.type];
              return (
                <div
                  key={n.id}
                  className={`flex gap-3 border-b border-border px-4 py-3 last:border-b-0 ${
                    !n.read ? "bg-primary-dim/30" : ""
                  }`}
                >
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-muted text-text-muted">
                    <Icon size={14} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-text">{n.title}</div>
                    <div className="mt-0.5 text-[12px] text-text-muted">{n.description}</div>
                    <div className="mt-1 text-[11px] text-text-faint">{n.time}</div>
                    {n.href && <a href={n.href} target="_blank" rel="noreferrer" className="mt-1 inline-flex text-[11px] font-medium text-primary underline">Open original</a>}
                  </div>
                  {!n.read && <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
