"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { formatDeadline } from "@/lib/format";
import type {
  AppConfig, AppEvent, AppUser, Category, EmailItem, Priority, Recommendation, Source, Task, TaskStatus,
} from "@/types";

type AuthStatus = "loading" | "authed" | "unauth" | "error";
export const SYNC_INTERVAL_MS = 5 * 60_000;

interface RawTask extends Omit<Task, "deadline" | "deadlineLabel"> {
  deadline: string | null;
}
interface RawEvent {
  id: string; title: string; start: string; end: string | null; allDay: boolean;
  location: string; meetLink: string; category: Category;
}
interface Ctx {
  status: AuthStatus;
  user: AppUser | null;
  config: AppConfig | null;
  tasks: Task[];
  events: AppEvent[]; // google events + task deadlines
  emails: EmailItem[];
  sources: Source[];
  recommendation: Recommendation | null;
  syncing: boolean;
  message: string | null;
  sync: () => Promise<boolean>;
  reloadWorkspace: () => Promise<void>;
  reloadEvents: () => Promise<void>;
  setTaskStatus: (id: string, status: TaskStatus) => Promise<void>;
  addTask: (input: { title: string; description?: string; category?: Category; priority?: Priority; deadline?: string | null }) => Promise<Task>;
  deleteTask: (id: string) => Promise<void>;
  logout: () => Promise<void>;
}

const NexusContext = createContext<Ctx | null>(null);

export function NexusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AppUser | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [gEvents, setGEvents] = useState<RawEvent[]>([]);
  const [emails, setEmails] = useState<EmailItem[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [recommendation, setRec] = useState<Recommendation | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const booted = useRef(false);
  const syncInFlight = useRef(false);

  useEffect(() => {
    const savedAppearance = window.localStorage.getItem("nexus-appearance");
    if (savedAppearance === "light" || savedAppearance === "dark") {
      document.documentElement.dataset.theme = savedAppearance;
    }
  }, []);

  const loadData = useCallback(async () => {
    const [t, e, m, s, r] = await Promise.all([
      api<RawTask[]>("/tasks"),
      api<RawEvent[]>("/events"),
      api<EmailItem[]>("/emails"),
      api<Source[]>("/sources"),
      api<Recommendation>("/recommendation"),
    ]);
    setTasks(t.map((x) => ({ ...x, deadline: x.deadline ?? "", deadlineLabel: formatDeadline(x.deadline) })));
    setGEvents(e);
    setEmails(m);
    setSources(s);
    setRec(r);
  }, []);

  const reloadWorkspace = useCallback(async () => {
    await loadData();
  }, [loadData]);

  const reloadEvents = useCallback(async () => {
    const latest = await api<RawEvent[]>("/events");
    setGEvents(latest);
  }, []);

  const sync = useCallback(async () => {
    const run = async (): Promise<boolean> => {
      if (syncInFlight.current) return true;
      syncInFlight.current = true;
      setSyncing(true);
      setMessage(null);
      try {
        const r = await api<{
          emailsFetched: number; newTasks: number; newSuggestions?: number; eventsSynced: number;
          pids?: {
            college?: { notifications?: number; aiSummarized?: number };
            classroom?: { notifications?: number; aiSummarized?: number };
          };
          partial: boolean; warnings: string[];
        }>("/sync", { method: "POST" }, 120_000);
        await loadData();
        const me = await api<AppUser>("/me");
        setUser(me);
        window.dispatchEvent(new Event("nexus-sync-complete"));
        const collegeNotices = r.pids?.college?.notifications ?? 0;
        const classroomUpdates = r.pids?.classroom?.notifications ?? 0;
        const aiSummaries = (r.pids?.college?.aiSummarized ?? 0) + (r.pids?.classroom?.aiSummarized ?? 0);
        const academicUpdates = [
          collegeNotices ? `${collegeNotices} new college notice${collegeNotices === 1 ? "" : "s"}` : "",
          classroomUpdates ? `${classroomUpdates} new Classroom update${classroomUpdates === 1 ? "" : "s"}` : "",
        ].filter(Boolean).join(" and ");
        const academicMessage = `${academicUpdates ? ` ${academicUpdates} are ready in Insights.` : ""}${aiSummaries ? ` Gemini summarized ${aiSummaries} new study update${aiSummaries === 1 ? "" : "s"}.` : ""}`;
        setMessage(
          r.partial
            ? `Sync incomplete. Last successful sync time was preserved. ${r.warnings.join(" · ")}${academicMessage}`
            : `Synced ${r.emailsFetched} emails and ${r.eventsSynced} events. ${r.newTasks} task(s) added${r.newSuggestions ? `; ${r.newSuggestions} Gmail action${r.newSuggestions === 1 ? "" : "s"} ready to review` : ""}.${academicMessage}` +
              (r.warnings.length ? ` ⚠ ${r.warnings.join(" · ")}` : "")
        );
        return !r.partial;
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          setStatus("unauth");
          setUser(null);
        } else {
          setMessage(`Sync failed: ${(e as Error).message}`);
        }
        return false;
      } finally {
        syncInFlight.current = false;
        setSyncing(false);
      }
    };

    if (navigator.locks) {
      return navigator.locks.request("nexus-ai-google-sync", { ifAvailable: true }, (lock) =>
        lock ? run() : Promise.resolve(true)
      );
    }
    return run();
  }, [loadData]);

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    (async () => {
      api<AppConfig>("/config").then(setConfig).catch(() => {});
      try {
        const me = await api<AppUser>("/me");
        setUser(me);
        setStatus("authed");
        try {
          await loadData();
        } catch {
          setMessage("Signed in, but workspace data could not be loaded. Try again shortly.");
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) setStatus("unauth");
        else {
          setStatus("error");
          setMessage("Backend not reachable. Is FastAPI running on :8000?");
        }
      }
    })();
  }, [loadData, sync]);

  useEffect(() => {
    if (status !== "authed") return;
    let timer = 0;
    let stopped = false;
    const schedule = (delay: number) => {
      timer = window.setTimeout(() => void run(), delay);
    };
    const run = async () => {
      if (stopped) return;
      if (document.visibilityState === "hidden") {
        schedule(SYNC_INTERVAL_MS);
        return;
      }
      await sync();
      schedule(SYNC_INTERVAL_MS);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        window.clearTimeout(timer);
        schedule(1000);
      }
    };
    schedule(1000);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [status, sync]);

  const setTaskStatus = useCallback(async (id: string, st: TaskStatus) => {
    setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, status: st } : t)));
    try {
      await api(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status: st }) });
      api<Recommendation>("/recommendation").then(setRec).catch(() => {});
    } catch {
      loadData();
    }
  }, [loadData]);

  const addTask = useCallback(async (input: { title: string; description?: string; category?: Category; priority?: Priority; deadline?: string | null }) => {
    const created = await api<RawTask>("/tasks", {
      method: "POST",
      body: JSON.stringify({
        title: input.title,
        description: input.description ?? "",
        category: input.category ?? "Informational",
        priority: input.priority ?? "medium",
        deadline: input.deadline ?? null,
      }),
    });
    const task: Task = {
      ...created,
      deadline: created.deadline ?? "",
      deadlineLabel: formatDeadline(created.deadline),
    };
    setTasks((current) => [task, ...current]);
    return task;
  }, []);

  const deleteTask = useCallback(async (id: string) => {
    setTasks((prev) => prev.filter((t) => t.id !== id));
    try {
      await api(`/tasks/${id}`, { method: "DELETE" });
    } catch {
      loadData();
    }
  }, [loadData]);

  const logout = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    setUser(null);
    setTasks([]);
    setStatus("unauth");
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- hard reload resets all state
    window.location.assign("/");
  }, []);

  const events = useMemo<AppEvent[]>(() => {
    const ev: AppEvent[] = gEvents.map((e) => ({ ...e, kind: "event" }));
    const dl: AppEvent[] = tasks
      .filter((t) => t.deadline && t.status !== "completed")
      .map((t) => ({
        id: `task-${t.id}`, title: `Due: ${t.title}`, start: t.deadline, end: null, allDay: false,
        location: "", meetLink: "", category: t.category, kind: "task" as const,
        priority: t.priority as Priority, taskId: t.id,
      }));
    return [...ev, ...dl].sort((a, b) => +new Date(a.start) - +new Date(b.start));
  }, [gEvents, tasks]);

  const value: Ctx = {
    status, user, config, tasks, events, emails, sources, recommendation, syncing, message,
    sync, reloadWorkspace, reloadEvents, setTaskStatus, addTask, deleteTask, logout,
  };
  return <NexusContext.Provider value={value}>{children}</NexusContext.Provider>;
}

export function useNexus() {
  const ctx = useContext(NexusContext);
  if (!ctx) throw new Error("useNexus must be used inside <NexusProvider>");
  return ctx;
}
