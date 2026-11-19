"use client";

import { useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import UserProfile from "@/components/UserProfile";
import Toggle from "@/components/Toggle";
import { useNexus } from "@/lib/store";
import { BrainCircuit, Moon, ShieldCheck, Sun, Trash2, LogOut } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { MemoryCandidate, NotificationPreferences, SystemStatus, UserMemory } from "@/types";
import { defaultNotificationPreferences, readLocalNotificationPreferences, writeLocalNotificationPreferences } from "@/lib/notification-preferences";

type GeminiProviderCheck = {
  status: "available" | "not_configured" | "consent_required" | "blocked_by_mode" | "provider_error";
  message: string;
  errorCode?: string;
  responseTimeMs?: number;
};

function SectionCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5 shadow-sm sm:p-6">
      <h3 className="font-display text-[15px] font-semibold text-text">{title}</h3>
      {description && <p className="mt-1 text-[12.5px] text-text-muted">{description}</p>}
      <div className="mt-5">{children}</div>
    </div>
  );
}

function Row({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-3 first:pt-0 last:pb-0">
      <div>
        <div className="text-[13.5px] font-medium text-text">{label}</div>
        {description && <div className="text-[12px] text-text-faint">{description}</div>}
      </div>
      {children}
    </div>
  );
}

export default function SettingsPage() {
  const { user, logout } = useNexus();
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [memoryText, setMemoryText] = useState("");
  const [memoryNotice, setMemoryNotice] = useState("");
  const [externalAiConsentOverride, setExternalAiConsentOverride] = useState<boolean | null>(null);
  const [consentSaving, setConsentSaving] = useState(false);
  const [consentError, setConsentError] = useState("");
  const [geminiChecking, setGeminiChecking] = useState(false);
  const [geminiCheck, setGeminiCheck] = useState<GeminiProviderCheck | null>(null);
  const [notificationPreferences, setNotificationPreferences] = useState<NotificationPreferences>(defaultNotificationPreferences);
  const [notificationSaving, setNotificationSaving] = useState(false);
  const [notificationError, setNotificationError] = useState("");
  const [notificationAvailable, setNotificationAvailable] = useState(false);
  const externalAiConsent = externalAiConsentOverride ?? user?.externalAiConsent ?? false;

  const changeAppearance = (next: "light" | "dark") => {
    window.localStorage.setItem("nexus-appearance", next);
    document.documentElement.dataset.theme = next;
  };

  useEffect(() => {
    api<UserMemory[]>("/memory").then(setMemories).catch(() => {});
    api<MemoryCandidate[]>("/memory/candidates").then(setCandidates).catch(() => {});
    api<SystemStatus>("/system/status").then(setStatus).catch(() => {});
    if (user?.id) {
      const localPreferences = readLocalNotificationPreferences(user.id);
      let active = true;
      queueMicrotask(() => {
        if (active) {
          setNotificationPreferences(localPreferences);
          setNotificationAvailable(true);
        }
      });
      api<NotificationPreferences>("/pids/notifications/preferences")
        .then((preferences) => {
          setNotificationPreferences(preferences);
          setNotificationAvailable(true);
          writeLocalNotificationPreferences(user.id, preferences);
          setNotificationError("");
        })
        .catch(() => {
          setNotificationError("Your settings are saved on this device. The daily brief appears while Nexus is open.");
        });
      return () => { active = false; };
    }
  }, [user?.id]);

  const updateNotificationPreference = async (patch: Partial<NotificationPreferences>) => {
    const next = { ...notificationPreferences, ...patch };
    setNotificationPreferences(next);
    setNotificationSaving(true);
    setNotificationError("Your settings are saved on this device. The daily brief appears while Nexus is open.");
    if (user?.id) writeLocalNotificationPreferences(user.id, next);
    if (patch.dailyBriefEnabled && typeof Notification !== "undefined" && Notification.permission === "default") {
      void Notification.requestPermission();
    }
    try {
      const saved = await api<NotificationPreferences>("/pids/notifications/preferences", {
        method: "PUT", body: JSON.stringify(next),
      });
      setNotificationPreferences(saved);
      setNotificationAvailable(true);
      setNotificationError("");
      if (user?.id) writeLocalNotificationPreferences(user.id, saved);
    } catch {
    } finally {
      setNotificationSaving(false);
    }
  };

  const updateExternalAiConsent = async (enabled: boolean) => {
    setConsentSaving(true);
    setConsentError("");
    try {
      await api("/privacy/ai-consent", {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      setExternalAiConsentOverride(enabled);
      setGeminiCheck(null);
    } catch (error) {
      setConsentError((error as Error).message);
      setExternalAiConsentOverride(externalAiConsent);
    } finally {
      setConsentSaving(false);
    }
  };

  const checkGeminiProvider = async () => {
    setGeminiChecking(true);
    setGeminiCheck(null);
    try {
      setGeminiCheck(await api<GeminiProviderCheck>("/ai/provider-check", { method: "POST" }, 60_000));
    } catch (error) {
      const timedOut = error instanceof ApiError && error.status === 408;
      setGeminiCheck({
        status: "provider_error",
        message: timedOut ? "The AI check took too long. Please try again; this check does not include your personal data." : "Could not check AI right now. Please try again.",
      });
    } finally {
      setGeminiChecking(false);
    }
  };

  const addMemory = async () => {
    const content = memoryText.trim();
    if (!content) return;
    await api("/memory", { method: "POST", body: JSON.stringify({ content, kind: "preference" }) });
    const next = await api<UserMemory[]>("/memory");
    setMemories(next);
    setMemoryText("");
    setMemoryNotice("Memory saved.");
  };

  const removeMemory = async (id: string) => {
    await api(`/memory/${id}`, { method: "DELETE" });
    setMemories((current) => current.filter((memory) => memory.id !== id));
  };

  const reviewCandidate = async (id: string, action: "approve" | "reject") => {
    await api(`/memory/candidates/${id}/${action}`, { method: "POST" });
    setCandidates((current) => current.filter((candidate) => candidate.id !== id));
    if (action === "approve") setMemories(await api<UserMemory[]>("/memory"));
    setMemoryNotice(action === "approve" ? "Memory approved." : "Memory rejected.");
  };

  return (
    <AppShell title="Settings" breadcrumb="Nexus AI / Settings">
      <div className="mx-auto flex max-w-2xl flex-col gap-5">
        <SectionCard title="Profile">
          <UserProfile name={user?.name ?? ""} email={user?.email ?? ""} />
          <p className="mt-4 text-[12.5px] text-text-muted">Profile details come from your Google account.</p>
        </SectionCard>

        <SectionCard title="Notifications" description="Choose what Nexus AI should notify you about.">
          <div className="divide-y divide-border">
            <Row label="Daily brief" description="Creates a local summary at the selected time while this browser workspace is open.">
              <Toggle checked={notificationPreferences.dailyBriefEnabled} disabled={!notificationAvailable || notificationSaving} onChange={(value) => void updateNotificationPreference({ dailyBriefEnabled: value })} />
            </Row>
            <Row label="Brief time" description="Uses the timezone shown below.">
              <input type="time" value={notificationPreferences.dailyBriefTime} disabled={!notificationAvailable || notificationSaving} onChange={(event) => setNotificationPreferences((current) => ({ ...current, dailyBriefTime: event.target.value }))} onBlur={() => void updateNotificationPreference({ dailyBriefTime: notificationPreferences.dailyBriefTime })} className="rounded-md border border-border bg-background px-2 py-1 text-xs text-text disabled:opacity-50" />
            </Row>
            <Row label="Timezone" description="Used to decide when the daily brief is ready.">
              <select aria-label="Timezone" value={notificationPreferences.timezone} disabled={!notificationAvailable || notificationSaving} onChange={(event) => void updateNotificationPreference({ timezone: event.target.value })} className="w-44 rounded-md border border-border bg-background px-2 py-1 text-xs text-text disabled:opacity-50">
                {["Asia/Kolkata", "UTC", "America/New_York", "America/Los_Angeles", "Europe/London"].map((zone) => <option key={zone} value={zone}>{zone.replaceAll("_", " ")}</option>)}
              </select>
            </Row>
            <Row label="Deadline reminders" description="Highlight upcoming and overdue deadlines in the notification panel.">
              <Toggle checked={notificationPreferences.deadlineReminders} disabled={!notificationAvailable || notificationSaving} onChange={(value) => void updateNotificationPreference({ deadlineReminders: value })} />
            </Row>
            <Row label="High-priority alerts" description="Show high-priority items in the notification panel.">
              <Toggle checked={notificationPreferences.highPriorityAlerts} disabled={!notificationAvailable || notificationSaving} onChange={(value) => void updateNotificationPreference({ highPriorityAlerts: value })} />
            </Row>
          </div>
          {notificationError && <p role="status" className="mt-3 text-xs text-text-muted">{notificationError}</p>}
        </SectionCard>

        <SectionCard title="Appearance">
          <div className="flex gap-3">
            <button
              onClick={() => changeAppearance("light")}
              id="appearance-light"
              className="flex flex-1 flex-col items-center gap-2 rounded-xl border-2 border-border p-4"
            >
              <Sun size={20} className="text-text-muted" />
              <span className="text-[12.5px] font-medium text-text">Light</span>
            </button>
            <button
              onClick={() => changeAppearance("dark")}
              id="appearance-dark"
              className="flex flex-1 flex-col items-center gap-2 rounded-xl border-2 border-border p-4"
            >
              <Moon size={20} className="text-text-muted" />
              <span className="text-[12.5px] font-medium text-text">Dark</span>
            </button>
          </div>
          <p className="mt-3 text-[11.5px] text-text-faint">
            Your choice is saved on this device and applies across the workspace.
          </p>
        </SectionCard>

        <SectionCard title="Workspace status" description="A quick look at the services used for your workspace.">
          <div className="grid gap-2 sm:grid-cols-2">
            <StatusRow icon={BrainCircuit} label="AI features" value={
              geminiCheck?.status === "available" ? "Ready" :
                status?.externalAi?.enabled ? "Ready" :
                  status?.externalAi?.configured ? "Turn it on below" : "Needs setup"
            } />
            <StatusRow icon={BrainCircuit} label="Workspace insights" value={
              status?.pids?.status === "connected" ? "Ready" : status?.pids ? "Needs attention" : "Checking…"
            } />
          </div>
          {status?.pids?.enabled && status.pids.status !== "connected" && (
            <p role="status" className="mt-3 rounded-lg border border-high/30 bg-high-bg px-3 py-2 text-[12px] text-high">
              Some workspace insights are temporarily unavailable. Try syncing again later.
            </p>
          )}
          {status?.externalAi && !status.externalAi.configured && (
            <div className="mt-3 rounded-lg border border-border bg-surface-muted px-3 py-3 text-[12px] text-text-muted">
              <p>AI setup is not finished yet. You can still use your saved tasks and workspace insights.</p>
            </div>
          )}
          <div className="mt-5 border-t border-border pt-5">
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                checked={externalAiConsent}
                disabled={consentSaving}
                onChange={(event) => void updateExternalAiConsent(event.target.checked)}
                className="mt-1 accent-primary"
              />
              <span>
                <span className="block text-[13.5px] font-medium text-text">Allow Google Gemini to process Nexus data</span>
                <span className="mt-1 block text-[12px] text-text-faint">
                  When on, Nexus can send relevant details from your emails, college or Classroom updates, tasks, calendar, and your question to Google Gemini to prepare answers and summaries. This setting does not affect the test below, which sends no personal data. Turn this off anytime to stop AI processing through Gemini.
                </span>
              </span>
            </label>
            {consentError && <p role="alert" className="mt-2 text-[12px] text-high">{consentError}</p>}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" onClick={() => void checkGeminiProvider()} disabled={geminiChecking} className="rounded-lg border border-border px-3 py-2 text-[12px] font-medium text-text transition hover:border-primary hover:text-primary disabled:opacity-60">
              {geminiChecking ? "Checking AI…" : "Check AI"}
            </button>
            <p className="text-[11px] text-text-faint">The test sends only a fixed message to check the connection. It never includes your inbox, tasks, calendar, or study updates.</p>
          </div>
          {geminiCheck && <p role="status" className={`mt-2 rounded-lg border px-3 py-2 text-[12px] ${geminiCheck.status === "available" ? "border-low/30 bg-low/5 text-low" : "border-border bg-surface-muted text-text-muted"}`}>
            {geminiCheck.message}
          </p>}
          <div className="mt-5 border-t border-border pt-5">
            <div className="mb-2 flex items-center justify-between">
              <div>
                <div className="text-[13.5px] font-medium text-text">Approved memories</div>
                <div className="text-[12px] text-text-faint">Only save preferences or facts you explicitly add.</div>
              </div>
              <ShieldCheck size={17} className="text-low" />
            </div>
            <div className="flex gap-2">
              <input value={memoryText} onChange={(event) => setMemoryText(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void addMemory()} placeholder="e.g. Prefer concise answers" className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-[12.5px] text-text outline-none focus:border-primary" />
              <button onClick={() => void addMemory()} className="rounded-lg bg-primary px-3 py-2 text-[12px] font-semibold text-white hover:bg-primary-hover">Save</button>
            </div>
            <div className="mt-3 space-y-2">
              {memories.length === 0 && <p className="text-[12px] text-text-faint">No approved memories yet.</p>}
              {memories.map((memory) => (
                <div key={memory.id} className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background px-3 py-2">
                  <span className="text-[12.5px] text-text">{memory.content}</span>
                  <button onClick={() => void removeMemory(memory.id)} className="rounded-md p-1.5 text-text-faint hover:bg-high-bg hover:text-high" aria-label={`Delete memory: ${memory.content}`}><Trash2 size={14} /></button>
                </div>
              ))}
            </div>
            {memoryNotice && <p className="mt-3 text-[12px] text-low">{memoryNotice}</p>}
            {candidates.length > 0 && (
              <div className="mt-5 border-t border-border pt-4">
                <div className="mb-2 text-[13px] font-medium text-text">Pending suggestions</div>
                <div className="space-y-2">
                  {candidates.map((candidate) => (
                    <div key={candidate.id} className="rounded-lg border border-border bg-background px-3 py-2">
                      <div className="text-[12.5px] text-text">{candidate.content}</div>
                      <div className="mt-2 flex gap-2">
                        <button onClick={() => void reviewCandidate(candidate.id, "approve")} className="rounded-md bg-primary px-2 py-1 text-[11px] font-semibold text-white">Approve</button>
                        <button onClick={() => void reviewCandidate(candidate.id, "reject")} className="rounded-md border border-border px-2 py-1 text-[11px] font-semibold text-text-muted">Reject</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </SectionCard>

        <SectionCard title="Account">
          <Row label="Sign out" description="Ends your session on this device.">
            <button onClick={logout} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[12.5px] font-medium text-high hover:bg-high-bg">
              <LogOut size={13} /> Logout
            </button>
          </Row>
        </SectionCard>
      </div>
    </AppShell>
  );
}

function StatusRow({ icon: Icon, label, value }: { icon: typeof BrainCircuit; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2.5">
      <Icon size={15} className="text-primary" />
      <span className="text-[12px] text-text-muted">{label}</span>
      <span className="ml-auto text-right text-[11px] font-medium text-text">{value}</span>
    </div>
  );
}
