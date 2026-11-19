"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import AppShell from "@/components/AppShell";
import TaskTable from "@/components/TaskTable";
import HoverContextPreview from "@/components/HoverContextPreview";
import { useNexus } from "@/lib/store";
import { api } from "@/lib/api";
import { ActionSuggestion, Category, Priority, SourceType, TaskStatus } from "@/types";
import { Plus, Search, SlidersHorizontal } from "lucide-react";

interface InboxCandidate {
  id: string;
  title: string;
  description: string;
  priority: string;
  dueDate: string | null;
  reviewState: string;
  createdAt?: string | null;
  sourceKind?: string;
  evidence: { excerpt?: string; nexusTaskId?: number; url?: string; sender?: string; subject?: string };
}

type SortKey = "deadline" | "priority" | "recent";

const categories: Category[] = ["Academic", "Administrative", "Career", "Informational"];
const priorities: Priority[] = ["high", "medium", "low"];
const statuses: TaskStatus[] = ["pending", "in-progress", "completed"];
const sourceTypes: SourceType[] = ["gmail", "calendar", "manual"];

const priorityRank: Record<Priority, number> = { high: 0, medium: 1, low: 2 };

function deadlineHasPassed(value: string | null | undefined): boolean {
  if (!value) return false;
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value)
    || /T00:00:00(?:\.0+)?(?:Z|[+-]00:00)?$/.test(value);
  const deadline = dateOnly
    ? new Date(`${value.slice(0, 10)}T23:59:59.999`)
    : new Date(value);
  return Number.isFinite(deadline.getTime()) && deadline.getTime() < Date.now();
}

export default function TasksPage() {
  const { tasks, addTask, reloadWorkspace, setTaskStatus, sync, syncing } = useNexus();
  const [candidates, setCandidates] = useState<InboxCandidate[]>([]);
  const [actionSuggestions, setActionSuggestions] = useState<ActionSuggestion[]>([]);
  const [candidateError, setCandidateError] = useState("");
  const [suggestionError, setSuggestionError] = useState("");
  const [candidateBusy, setCandidateBusy] = useState<string | null>(null);
  const [reanalyzing, setReanalyzing] = useState(false);
  const [refreshingSuggestions, setRefreshingSuggestions] = useState(false);
  const [analysisNotice, setAnalysisNotice] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | "all">("all");
  const [priority, setPriority] = useState<Priority | "all">("all");
  const [status, setStatus] = useState<TaskStatus | "all">("all");
  const [source, setSource] = useState<SourceType | "all">("all");
  const [sort, setSort] = useState<SortKey>("deadline");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [deadline, setDeadline] = useState("");
  const [newCategory, setNewCategory] = useState<Category>("Informational");
  const [newPriority, setNewPriority] = useState<Priority>("medium");
  const [formError, setFormError] = useState("");

  const loadCandidates = useCallback(async () => {
    const [overviewResult, suggestionsResult] = await Promise.allSettled([
      api<{ tasks: InboxCandidate[] }>("/pids/overview"),
      api<ActionSuggestion[]>("/action-suggestions"),
    ]);
    if (overviewResult.status === "fulfilled") {
      setCandidates(overviewResult.value.tasks.filter((task) => task.sourceKind !== "gmail" && (task.reviewState === "needs_review" || (task.reviewState === "approved" && !task.evidence.nexusTaskId))));
      setCandidateError("");
    } else {
      setCandidates([]);
      setCandidateError("Academic inbox suggestions could not be loaded. Gmail suggestions are checked separately.");
    }
    if (suggestionsResult.status === "fulfilled") {
      setActionSuggestions(suggestionsResult.value);
      setSuggestionError("");
    } else {
      setActionSuggestions([]);
      setSuggestionError("Saved Gmail suggestions could not be loaded.");
    }
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      api<{ tasks: InboxCandidate[] }>("/pids/overview"),
      api<ActionSuggestion[]>("/action-suggestions"),
    ]).then(([overviewResult, suggestionsResult]) => {
      if (!active) return;
      if (overviewResult.status === "fulfilled") {
        setCandidates(overviewResult.value.tasks.filter((task) => task.sourceKind !== "gmail" && (task.reviewState === "needs_review" || (task.reviewState === "approved" && !task.evidence.nexusTaskId))));
        setCandidateError("");
      } else {
        setCandidates([]);
        setCandidateError("Academic inbox suggestions could not be loaded. Gmail suggestions are checked separately.");
      }
      if (suggestionsResult.status === "fulfilled") {
        setActionSuggestions(suggestionsResult.value);
        setSuggestionError("");
      } else {
        setActionSuggestions([]);
        setSuggestionError("Saved Gmail suggestions could not be loaded.");
      }
    });
    return () => { active = false; };
  }, []);

  const reviewActionSuggestion = async (suggestion: ActionSuggestion, action: "approve" | "reject") => {
    setCandidateBusy(suggestion.id);
    setSuggestionError("");
    try {
      await api(`/action-suggestions/${encodeURIComponent(suggestion.id)}/${action}`, { method: "POST" });
      if (action === "approve") await reloadWorkspace();
      await loadCandidates();
    } catch (error) {
      setSuggestionError((error as Error).message || "Could not update this Gmail suggestion.");
    } finally {
      setCandidateBusy(null);
    }
  };

  const reanalyzeStoredEmails = async () => {
    setReanalyzing(true);
    setAnalysisNotice("");
    setSuggestionError("");
    try {
      let offset = 0;
      let snapshotMaxId: number | null = null;
      let pageLimit = 20;
      let useGemini = true;
      let total = 0;
      let processed = 0;
      let suggestionsChanged = 0;
      do {
        const params = new URLSearchParams({
          limit: String(pageLimit), offset: String(offset), useGemini: String(useGemini),
        });
        if (snapshotMaxId !== null) params.set("snapshotMaxId", String(snapshotMaxId));
        const result = await api<{
          processed: number; total: number; nextOffset: number | null; hasMore: boolean;
          snapshotMaxId: number | null;
          suggestionsCreated: number; suggestionsUpdated: number;
          geminiProcessed: number; rulesProcessed: number; geminiStatus: string;
        }>(`/emails/reanalyze?${params}`, { method: "POST" }, 60_000);
        total = result.total;
        snapshotMaxId = result.snapshotMaxId;
        processed += result.processed;
        suggestionsChanged += result.suggestionsCreated + result.suggestionsUpdated;
        offset = result.nextOffset ?? total;
        setAnalysisNotice(`Reviewing saved emails: ${Math.min(processed, total)} of ${total}…`);
        if (result.hasMore && useGemini && ["key_missing", "consent_required", "provider_unavailable"].includes(result.geminiStatus)) {
          useGemini = false;
          pageLimit = 500;
        }
        if (!result.processed) break;
      } while (offset < total);

      setAnalysisNotice(`Reviewed ${processed} saved emails; ${suggestionsChanged} task suggestions are ready to review.`);
      await loadCandidates();
    } catch (error) {
      setSuggestionError((error as Error).message || "Could not re-check saved emails.");
    } finally {
      setReanalyzing(false);
    }
  };

  const refreshSuggestions = async () => {
    setRefreshingSuggestions(true);
    try {
      await sync();
      await reanalyzeStoredEmails();
    } finally {
      setRefreshingSuggestions(false);
    }
  };

  const saveCandidate = async (candidate: InboxCandidate) => {
    setCandidateBusy(candidate.id);
    setCandidateError("");
    try {
      if (candidate.reviewState === "needs_review") {
        await api(`/pids/tasks/${encodeURIComponent(candidate.id)}/review`, {
          method: "POST",
          body: JSON.stringify({ action: "approve" }),
        });
      }
      await api(`/pids/tasks/${encodeURIComponent(candidate.id)}/create-task`, { method: "POST" });
      await Promise.all([reloadWorkspace(), loadCandidates()]);
    } catch (error) {
      setCandidateError((error as Error).message || "Could not save this inbox suggestion. Please retry.");
      void loadCandidates();
    } finally {
      setCandidateBusy(null);
    }
  };

  const dismissCandidate = async (candidate: InboxCandidate) => {
    setCandidateBusy(candidate.id);
    setCandidateError("");
    try {
      await api(`/pids/tasks/${encodeURIComponent(candidate.id)}/review`, {
        method: "POST", body: JSON.stringify({ action: "reject" }),
      });
      await loadCandidates();
    } catch (error) {
      setCandidateError((error as Error).message || "Could not dismiss this suggestion.");
    } finally {
      setCandidateBusy(null);
    }
  };

  const submitTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setFormError("");
    try {
      await addTask({
        title: title.trim(),
        description: description.trim(),
        category: newCategory,
        priority: newPriority,
        deadline: deadline ? new Date(`${deadline}T23:59:00`).toISOString() : null,
      });
      setTitle("");
      setDescription("");
      setDeadline("");
      setNewCategory("Informational");
      setNewPriority("medium");
      setAddOpen(false);
    } catch (error) {
      setFormError((error as Error).message || "Could not save this task.");
    } finally {
      setSaving(false);
    }
  };

  const filtered = useMemo(() => {
    let result = tasks.filter((t) => {
      if (query && !t.title.toLowerCase().includes(query.toLowerCase())) return false;
      if (category !== "all" && t.category !== category) return false;
      if (priority !== "all" && t.priority !== priority) return false;
      if (status !== "all" && t.status !== status) return false;
      if (source !== "all" && t.source !== source) return false;
      return true;
    });

    result = [...result].sort((a, b) => {
      if (sort === "priority") return priorityRank[a.priority] - priorityRank[b.priority];
      if (sort === "recent") return +new Date(b.createdAt) - +new Date(a.createdAt);
      const far = "2100-01-01";
      return +new Date(a.deadline || far) - +new Date(b.deadline || far);
    });

    return result;
  }, [tasks, query, category, priority, status, source, sort]);

  const pastDeadlineTasks = useMemo(
    () => tasks.filter((task) => deadlineHasPassed(task.deadline)),
    [tasks],
  );
  const visibleActionSuggestions = actionSuggestions.filter((suggestion) => !deadlineHasPassed(suggestion.deadline));
  const visibleCandidates = candidates.filter((candidate) => !deadlineHasPassed(candidate.dueDate));

  return (
    <AppShell title="All Tasks" breadcrumb="Nexus AI / Tasks">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-xl font-semibold text-text">Tasks &amp; Goals</h2>
          <p className="mt-1 text-[12px] text-text-faint">Add the task name now; fill in details only if you want.</p>
        </div>
        <button onClick={() => { setAddOpen((open) => !open); setFormError(""); }} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-[13px] font-semibold text-white transition hover:bg-primary-hover">
          <Plus size={16} /> {addOpen ? "Close" : "Add task"}
        </button>
      </div>

      {addOpen && (
        <form onSubmit={(event) => void submitTask(event)} className="mb-5 rounded-xl border border-primary/25 bg-surface p-4 shadow-sm sm:p-5">
          <label className="block text-[12px] font-semibold text-text" htmlFor="new-task-title">What do you need to do?</label>
          <input id="new-task-title" autoFocus required maxLength={512} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="e.g. Finish DBMS assignment" className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-[13px] text-text outline-none placeholder:text-text-faint focus:border-primary" />
          <details className="mt-3 rounded-lg border border-border bg-background px-3 py-2.5">
            <summary className="cursor-pointer text-[12px] font-medium text-text-muted">Add details (optional)</summary>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <label className="text-[11px] font-medium text-text-muted">Deadline<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} className="mt-1 block w-full rounded-md border border-border bg-surface px-2 py-2 text-[12px] text-text" /></label>
              <label className="text-[11px] font-medium text-text-muted">Priority<select value={newPriority} onChange={(event) => setNewPriority(event.target.value as Priority)} className="mt-1 block w-full rounded-md border border-border bg-surface px-2 py-2 text-[12px] text-text">{priorities.map((value) => <option key={value} value={value}>{value === "medium" ? "Normal" : value[0].toUpperCase() + value.slice(1)}</option>)}</select></label>
              <label className="text-[11px] font-medium text-text-muted">Category<select value={newCategory} onChange={(event) => setNewCategory(event.target.value as Category)} className="mt-1 block w-full rounded-md border border-border bg-surface px-2 py-2 text-[12px] text-text">{categories.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
              <label className="text-[11px] font-medium text-text-muted sm:col-span-2 lg:col-span-1">Notes<textarea maxLength={4000} rows={1} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Anything else to remember?" className="mt-1 block w-full resize-y rounded-md border border-border bg-surface px-2 py-2 text-[12px] text-text placeholder:text-text-faint" /></label>
            </div>
          </details>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <p className="text-[11px] text-text-faint">If you skip the details, we save it as a normal priority task with no deadline.</p>
            <button disabled={saving || !title.trim()} className="rounded-lg bg-primary px-4 py-2 text-[12px] font-semibold text-white hover:bg-primary-hover disabled:opacity-50">{saving ? "Saving…" : "Save task"}</button>
          </div>
          {formError && <p role="alert" className="mt-2 text-[12px] text-high">{formError}</p>}
        </form>
      )}

      <div className="flex flex-col">
      <section id="inbox-suggestions" className="order-2 mb-6 scroll-mt-5 rounded-xl border border-primary/25 bg-primary/5 p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h3 className="font-display text-lg font-semibold text-text">Suggested from your inbox</h3><p className="mt-1 text-[12px] text-text-muted">Refresh suggestions pehle Gmail sync karega, phir saved emails aur study updates ko dobara check karega. Expired deadlines yahan nahi dikhengi.</p></div>
          <div className="flex flex-wrap items-center gap-3"><button onClick={() => void refreshSuggestions()} disabled={refreshingSuggestions || reanalyzing || syncing} className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text-muted disabled:opacity-50">{refreshingSuggestions || syncing ? "Syncing inbox…" : reanalyzing ? "Checking saved emails…" : "Refresh suggestions"}</button></div>
        </div>
        {analysisNotice && <p role="status" className="mt-3 rounded-lg bg-low-bg px-3 py-2 text-xs text-low">{analysisNotice}</p>}
        {suggestionError && <p role="alert" className="mt-3 text-xs text-high">{suggestionError}</p>}
        {visibleActionSuggestions.length > 0 && <div className="mt-4 space-y-2"><h4 className="text-xs font-semibold uppercase tracking-wide text-text-faint">Gmail task suggestions</h4>{visibleActionSuggestions.map((suggestion) => <HoverContextPreview key={suggestion.id} title={suggestion.title} description={suggestion.description} meta={[`${suggestion.priority} priority`, suggestion.deadline ? `Due ${new Date(suggestion.deadline).toLocaleString()}` : "No deadline found", suggestion.sender, suggestion.subject]} emailId={suggestion.emailId} createdAt={suggestion.createdAt} emailUrl={suggestion.gmailUrl}><article className="rounded-lg border border-border bg-surface p-3"><div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><h5 className="text-sm font-semibold text-text">{suggestion.title}</h5><p className="mt-1 text-xs text-text-muted">{suggestion.description}</p><p className="mt-1 text-[11px] text-text-faint">{suggestion.sender} · {suggestion.subject}</p></div><span className="rounded-full bg-surface-muted px-2 py-1 text-[10px] capitalize text-text-muted">{suggestion.priority} · {suggestion.deadline ? `Due ${new Date(suggestion.deadline).toLocaleString()}` : "No deadline found"}</span></div><div className="mt-3 flex flex-wrap gap-2"><button onClick={() => void reviewActionSuggestion(suggestion, "approve")} disabled={candidateBusy === suggestion.id} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">{candidateBusy === suggestion.id ? "Saving…" : "Add to my tasks"}</button><button onClick={() => void reviewActionSuggestion(suggestion, "reject")} disabled={candidateBusy === suggestion.id} className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text-muted disabled:opacity-50">Not a task</button><a href={suggestion.gmailUrl || `https://mail.google.com/mail/u/0/#search/${encodeURIComponent(`subject:${suggestion.subject}`)}`} target="_blank" rel="noreferrer" className="inline-flex items-center px-2 text-xs font-medium text-primary underline">Open email</a></div></article></HoverContextPreview>)}</div>}
        {candidateError && <p role="alert" className="mt-3 text-xs text-high">{candidateError}</p>}
        <div className="mt-3 space-y-2">
          {visibleCandidates.map((candidate) => <HoverContextPreview key={candidate.id} title={candidate.title} description={candidate.description} meta={[candidate.sourceKind?.replaceAll("_", " ") || "Study update", `${candidate.priority} priority`, candidate.dueDate ? `Due ${candidate.dueDate}` : "No deadline found", candidate.evidence.sender || "", candidate.evidence.subject || ""]} createdAt={candidate.createdAt} emailUrl={candidate.evidence.url}><article className="rounded-lg border border-border bg-surface p-3">
            <div className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><h4 className="text-sm font-semibold text-text">{candidate.title}</h4><p className="mt-1 text-xs text-text-muted">{candidate.description}</p></div><span className="rounded-full bg-surface-muted px-2 py-1 text-[10px] capitalize text-text-muted">{candidate.priority} · {candidate.dueDate ? `Due ${candidate.dueDate}` : "No deadline found"}</span></div>
            {(candidate.evidence.sender || candidate.evidence.subject) && <p className="mt-2 text-[11px] text-text-faint">{[candidate.evidence.sender, candidate.evidence.subject].filter(Boolean).join(" · ")}</p>}
            {candidate.evidence.excerpt && <blockquote className="mt-2 border-l-2 border-primary/40 pl-2 text-xs text-text-faint">“{candidate.evidence.excerpt}”</blockquote>}
            {candidate.evidence.url && <a href={candidate.evidence.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex text-xs font-medium text-primary underline">Open original email</a>}
            <div className="mt-3 flex gap-2"><button onClick={() => void saveCandidate(candidate)} disabled={candidateBusy === candidate.id} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">{candidateBusy === candidate.id ? "Saving…" : "Add to my tasks"}</button><button onClick={() => void dismissCandidate(candidate)} disabled={candidateBusy === candidate.id} className="rounded-lg border border-border px-3 py-2 text-xs font-medium text-text-muted disabled:opacity-50">Not a task</button></div>
          </article></HoverContextPreview>)}
          {!candidateError && visibleCandidates.length === 0 && <p className="rounded-lg border border-border bg-surface px-3 py-3 text-xs text-text-muted">No current college or Classroom actions are waiting for review. Suggestions with passed deadlines are hidden here; saved tasks remain in the deadline tracker below.</p>}
        </div>
      </section>

      <div className="order-1">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 sm:w-80">
          <Search size={16} className="shrink-0 text-text-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tasks..."
            className="w-full bg-transparent text-[13.5px] text-text outline-none placeholder:text-text-faint"
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setFiltersOpen((o) => !o)}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-2 text-[13px] font-medium sm:hidden ${
              filtersOpen ? "border-primary bg-primary-dim text-primary" : "border-border bg-surface text-text"
            }`}
          >
            <SlidersHorizontal size={14} /> Filters
          </button>
        </div>
      </div>

      <div className={`mb-5 flex-wrap gap-2 sm:flex ${filtersOpen ? "flex" : "hidden"}`}>
        <FilterSelect label="Category" value={category} onChange={setCategory} options={categories} />
        <FilterSelect label="Priority" value={priority} onChange={setPriority} options={priorities} capitalize />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={statuses} capitalize />
        <FilterSelect label="Source" value={source} onChange={setSource} options={sourceTypes} capitalize />

        <div className="ml-0 flex items-center gap-1.5 sm:ml-auto">
          <span className="text-[12px] text-text-faint">Sort by</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[12.5px] font-medium text-text outline-none focus:border-primary"
          >
            <option value="deadline">Deadline</option>
            <option value="priority">Priority</option>
            <option value="recent">Recently Added</option>
          </select>
        </div>
      </div>

      <div className="mb-3 text-[12.5px] text-text-faint">{filtered.length} tasks</div>

      <TaskTable tasks={filtered} />

      <section id="deadline-tracker" className="mt-8 rounded-xl border border-border bg-surface p-4 shadow-sm sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-display text-lg font-semibold text-text">Past-deadline tracker</h3>
            <p className="mt-1 text-[12px] text-text-muted">Overdue tasks stay visible here until you mark them complete.</p>
          </div>
          <span className="rounded-full bg-high-bg px-2.5 py-1 text-xs font-semibold text-high">{pastDeadlineTasks.filter((task) => task.status !== "completed").length} open past deadline</span>
        </div>
        {pastDeadlineTasks.length === 0 ? (
          <p className="mt-4 rounded-lg border border-dashed border-border px-3 py-5 text-center text-sm text-text-muted">No tasks have passed their deadline. Nice work!</p>
        ) : (
          <div className="mt-4 space-y-2">
            {pastDeadlineTasks.map((task) => {
              const completed = task.status === "completed";
              const inProgress = task.status === "in-progress";
              const tone = completed
                ? "border-low/30 bg-low-bg"
                : inProgress ? "border-orange-300 bg-orange-50 dark:border-orange-800 dark:bg-orange-950/30" : "border-high/30 bg-high-bg";
              const badge = completed
                ? "Completed / submitted"
                : inProgress ? "Late submission possible · in progress" : "Deadline passed";
              return <article key={task.id} className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 ${tone}`}>
                <div className="min-w-0 flex-1">
                  <HoverContextPreview title={task.title} description={task.description} meta={[`Due ${task.deadlineLabel}`, `${task.priority} priority`, task.status.replace("-", " "), task.emailSender || "", task.emailSubject || ""]} emailId={task.emailId} createdAt={task.createdAt}>
                    <div className="cursor-help">
                      <p className="truncate text-sm font-semibold text-text">{task.title}</p>
                      <p className="mt-1 text-[11px] text-text-muted">Due {task.deadlineLabel} · {task.priority} priority</p>
                    </div>
                  </HoverContextPreview>
                </div>
                <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${completed ? "bg-low-bg text-low" : inProgress ? "bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-200" : "bg-high-bg text-high"}`}>{badge}</span>
                {!completed && <div className="flex flex-wrap gap-2">
                  {!inProgress && <button onClick={() => void setTaskStatus(task.id, "in-progress")} className="rounded-lg border border-orange-400/50 px-3 py-1.5 text-[11px] font-semibold text-orange-800 hover:bg-orange-100 dark:text-orange-200 dark:hover:bg-orange-900/50">Can still submit? Mark in progress</button>}
                  {inProgress && <button onClick={() => void setTaskStatus(task.id, "pending")} className="rounded-lg border border-high/40 px-3 py-1.5 text-[11px] font-semibold text-high hover:bg-high-bg">Move back to overdue</button>}
                  <button onClick={() => void setTaskStatus(task.id, "completed")} className="rounded-lg bg-low px-3 py-1.5 text-[11px] font-semibold text-white hover:opacity-90">Mark submitted / complete</button>
                </div>}
              </article>;
            })}
          </div>
        )}
        <p className="mt-3 text-[11px] text-text-faint">Red = still open and overdue. Orange = you marked it as in progress, so late submission may still be possible. Green = marked completed by you.</p>
      </section>
      </div>
      </div>
    </AppShell>
  );
}

function FilterSelect<T extends string>({
  label,
  value,
  onChange,
  options,
  capitalize = false,
}: {
  label: string;
  value: T | "all";
  onChange: (v: T | "all") => void;
  options: T[];
  capitalize?: boolean;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T | "all")}
      className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[12.5px] font-medium text-text outline-none focus:border-primary"
    >
      <option value="all">All {label}</option>
      {options.map((opt) => (
        <option key={opt} value={opt} className={capitalize ? "capitalize" : ""}>
          {opt.charAt(0).toUpperCase() + opt.slice(1)}
        </option>
      ))}
    </select>
  );
}
