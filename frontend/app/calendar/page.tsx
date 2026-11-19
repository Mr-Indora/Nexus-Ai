"use client";

import { useMemo, useState, type FormEvent } from "react";
import AppShell from "@/components/AppShell";
import { useNexus } from "@/lib/store";
import { api } from "@/lib/api";
import { dateKey, timeOf } from "@/lib/format";
import type { AppEvent } from "@/types";
import { categoryColor } from "@/lib/display";
import { ChevronLeft, ChevronRight, Pencil, Video, X } from "lucide-react";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const toISODate = dateKey;

export default function CalendarPage() {
  const { events: calendarEvents, reloadEvents } = useNexus();
  const [view, setView] = useState<"month" | "week" | "day">("month");
  const [cursor, setCursor] = useState(new Date());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [eventEditorOpen, setEventEditorOpen] = useState(false);
  const [eventForm, setEventForm] = useState({ title: "", start: "", end: "", location: "", description: "", allDay: false });
  const [descriptionTouched, setDescriptionTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [eventMessage, setEventMessage] = useState("");

  const monthGrid = useMemo(() => {
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const firstDay = new Date(year, month, 1);
    const startOffset = firstDay.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const cells: (Date | null)[] = [];
    for (let i = 0; i < startOffset; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d));
    while (cells.length % 7 !== 0) cells.push(null);
    return cells;
  }, [cursor]);

  const today = useMemo(() => new Date(), []);
  const eventsByDate = useMemo(() => {
    const map: Record<string, AppEvent[]> = {};
    calendarEvents.forEach((ev) => {
      const k = dateKey(new Date(ev.start));
      map[k] = map[k] ? [...map[k], ev] : [ev];
    });
    return map;
  }, [calendarEvents]);

  const upcoming = calendarEvents.filter((ev) => new Date(ev.start) >= today).slice(0, 7);

  const toLocalInput = (value: string) => {
    const date = new Date(value);
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(0, 16);
  };

  const toLocalDateInput = (value: string) => dateKey(new Date(value));
  const addLocalDays = (value: string, days: number) => {
    const date = new Date(`${value}T00:00:00`);
    date.setDate(date.getDate() + days);
    return dateKey(date);
  };

  const openEditEvent = (ev: AppEvent) => {
    setEditingId(ev.id);
    setEventEditorOpen(true);
    setDescriptionTouched(false);
    setEventMessage("");
    setEventForm({
      title: ev.title,
      start: ev.allDay ? toLocalDateInput(ev.start) : toLocalInput(ev.start),
      end: ev.allDay ? addLocalDays(toLocalDateInput(ev.end || ev.start), -1) : toLocalInput(ev.end || ev.start),
      location: ev.location || "", description: "", allDay: ev.allDay,
    });
  };

  const saveEvent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingId) return;
    const startValue = eventForm.allDay ? new Date(`${eventForm.start}T00:00:00`) : new Date(eventForm.start);
    const endValue = eventForm.allDay ? new Date(`${eventForm.end}T00:00:00`) : new Date(eventForm.end);
    if (eventForm.allDay ? startValue.getTime() > endValue.getTime() : startValue.getTime() >= endValue.getTime()) {
      setEventMessage("The end time must be later than the start time.");
      return;
    }
    setSaving(true);
    setEventMessage("");
    try {
      const payload = {
        title: eventForm.title.trim(),
        start: eventForm.allDay ? eventForm.start : new Date(eventForm.start).toISOString(),
        end: eventForm.allDay ? addLocalDays(eventForm.end, 1) : new Date(eventForm.end).toISOString(),
        all_day: eventForm.allDay,
        location: eventForm.location.trim(),
        ...(!editingId || descriptionTouched ? { description: eventForm.description.trim() } : {}),
      };
      await api(`/calendar/events/${encodeURIComponent(editingId)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }, 30_000);
      // The event write has succeeded at Google and in Nexus. Refresh only
      // Calendar so an unrelated mail/source endpoint cannot make a saved
      // event look like a failed save.
      setEventEditorOpen(false);
      setEditingId(null);
      try {
        await reloadEvents();
        setEventMessage("");
      } catch {
        setEventMessage("Event saved to Google Calendar. Reload Calendar if it does not appear yet.");
      }
    } catch (error) {
      setEventMessage(error instanceof Error ? error.message : "Could not save this event. Reconnect Google Calendar and try again.");
    } finally {
      setSaving(false);
    }
  };

  const visibleDates = useMemo(() => {
    if (view === "month") return monthGrid;
    if (view === "day") return [cursor];
    const weekStart = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() - cursor.getDay());
    return Array.from({ length: 7 }, (_, index) => new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + index));
  }, [cursor, monthGrid, view]);

  const moveCursor = (delta: number) => {
    if (view === "month") setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + delta, 1));
    else if (view === "week") setCursor(new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + delta * 7));
    else setCursor(new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + delta));
  };

  const periodTitle = view === "month"
    ? `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`
    : view === "day"
      ? cursor.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })
      : `${visibleDates[0]?.toLocaleDateString("en-IN", { day: "numeric", month: "short" })} – ${visibleDates[6]?.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}`;

  return (
    <AppShell title="Calendar" breadcrumb="Nexus AI / Calendar">
      <p className="mb-4 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3 text-sm text-text-muted">Review your Google Calendar events and edit an event when needed.</p>
      {eventMessage && !eventEditorOpen && <div role="status" className="mb-4 rounded-lg border border-border bg-surface px-4 py-3 text-xs text-text-muted">{eventMessage}</div>}
      {eventEditorOpen ? <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-3 sm:p-6">
        <form onSubmit={(event) => void saveEvent(event)} className="w-full max-w-xl rounded-2xl border border-border bg-surface p-5 shadow-2xl">
          <div className="mb-4 flex items-start justify-between"><div><h2 className="font-display text-lg font-semibold text-text">Edit calendar event</h2><p className="mt-1 text-xs text-text-muted">Changes are saved to your Google Calendar.</p></div><button type="button" onClick={() => { setEditingId(null); setEventEditorOpen(false); setEventForm({ title: "", start: "", end: "", location: "", description: "", allDay: false }); }} aria-label="Close event editor" className="rounded-lg border border-border p-2 text-text-muted hover:bg-surface-muted"><X size={15} /></button></div>
          <label className="block text-xs font-medium text-text-muted">Event title<input required maxLength={512} value={eventForm.title} onChange={(event) => setEventForm((current) => ({ ...current, title: event.target.value }))} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text" /></label>
          <label className="mt-3 inline-flex items-center gap-2 text-xs font-medium text-text-muted"><input type="checkbox" checked={eventForm.allDay} onChange={(event) => setEventForm((current) => ({ ...current, allDay: event.target.checked, start: event.target.checked ? current.start.slice(0, 10) : `${current.start.slice(0, 10)}T09:00`, end: event.target.checked ? current.end.slice(0, 10) : `${current.end.slice(0, 10)}T10:00` }))} className="h-4 w-4 accent-primary" />All day</label>
          <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium text-text-muted">Starts<input type={eventForm.allDay ? "date" : "datetime-local"} required value={eventForm.start} onChange={(event) => setEventForm((current) => ({ ...current, start: event.target.value }))} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text" /></label><label className="text-xs font-medium text-text-muted">Ends<input type={eventForm.allDay ? "date" : "datetime-local"} required value={eventForm.end} onChange={(event) => setEventForm((current) => ({ ...current, end: event.target.value }))} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text" /></label></div>
          <label className="mt-3 block text-xs font-medium text-text-muted">Location<input maxLength={512} value={eventForm.location} onChange={(event) => setEventForm((current) => ({ ...current, location: event.target.value }))} className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text" /></label>
          <label className="mt-3 block text-xs font-medium text-text-muted">Description<textarea maxLength={8000} rows={3} value={eventForm.description} onChange={(event) => { setDescriptionTouched(true); setEventForm((current) => ({ ...current, description: event.target.value })); }} className="mt-1 w-full resize-y rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text" /></label>
          {eventMessage && <p role="alert" className="mt-3 rounded-lg bg-high-bg px-3 py-2 text-xs text-high">{eventMessage}</p>}
          <div className="mt-4 flex justify-end gap-2"><button type="button" onClick={() => { setEditingId(null); setEventEditorOpen(false); setEventForm({ title: "", start: "", end: "", location: "", description: "", allDay: false }); }} className="rounded-lg border border-border px-3.5 py-2 text-xs font-medium text-text-muted">Cancel</button><button disabled={saving} className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white disabled:opacity-50">{saving ? "Saving…" : "Save changes"}</button></div>
        </form>
      </div> : null}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_300px]">
        {/* Main calendar */}
        <div className="rounded-xl border border-border bg-surface p-4 shadow-sm sm:p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h2 className="font-display text-lg font-semibold text-text">{periodTitle}</h2>
              <div className="flex items-center gap-1">
                <button aria-label={`Previous ${view}`} onClick={() => moveCursor(-1)} className="rounded-md p-1.5 text-text-muted hover:bg-surface-muted">
                  <ChevronLeft size={16} />
                </button>
                <button aria-label={`Next ${view}`} onClick={() => moveCursor(1)} className="rounded-md p-1.5 text-text-muted hover:bg-surface-muted">
                  <ChevronRight size={16} />
                </button>
                <button onClick={() => setCursor(new Date())} className="ml-1 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-text-muted hover:bg-surface-muted">Today</button>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border border-border bg-surface-muted p-0.5 text-[12.5px] font-medium">
                {(["month", "week", "day"] as const).map((v) => (
                  <button
                    key={v}
                    onClick={() => setView(v)}
                    className={`rounded-md px-3 py-1 capitalize transition-colors ${
                      view === v ? "bg-surface text-primary shadow-sm" : "text-text-muted"
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {view !== "day" && <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-medium text-text-faint">
            {WEEKDAYS.map((d) => (
              <div key={d} className="py-1.5">{d}</div>
            ))}
          </div>}

          <div className={`grid gap-1 ${view === "day" ? "grid-cols-1" : "grid-cols-7"}`}>
            {visibleDates.map((date, i) => {
              if (!date) return <div key={i} className="min-h-[92px] rounded-lg" />;
              const iso = toISODate(date);
              const isToday = iso === toISODate(today);
              const dayEvents = eventsByDate[iso] || [];
              return (
                <div
                  key={i}
                  className={`${view === "day" ? "min-h-[280px]" : view === "week" ? "min-h-[150px]" : "min-h-[92px]"} rounded-lg border p-1.5 sm:p-2 ${
                    isToday ? "border-primary bg-primary-dim/40" : "border-border hover:bg-surface-muted/50"
                  }`}
                >
                  <span
                    className={`text-[11.5px] font-medium ${
                      isToday ? "flex h-5 w-5 items-center justify-center rounded-full bg-primary text-white" : "text-text-muted"
                    }`}
                  >
                    {date.getDate()}
                  </span>
                  <div className={`mt-1 space-y-1 ${view === "day" ? "max-h-[min(65vh,720px)] overflow-y-auto pr-1" : ""}`}>
                    {(view === "day" ? dayEvents : dayEvents.slice(0, view === "week" ? 4 : 2)).map((ev) => (
                      <button
                        key={ev.id}
                        type="button"
                        disabled={ev.kind !== "event"}
                        onClick={() => openEditEvent(ev)}
                        aria-label={ev.kind === "event" ? `Edit ${ev.title}` : ev.title}
                        className={`${view === "day" ? "flex items-center justify-between gap-3 rounded-md px-3 py-2 text-xs sm:text-sm" : "truncate rounded px-1 py-0.5 text-[9.5px] sm:text-[10px]"} w-full text-left font-medium ${ev.kind === "event" ? "cursor-pointer hover:opacity-80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary" : "cursor-default"}`}
                        style={{ color: categoryColor[ev.category], backgroundColor: `${categoryColor[ev.category]}18` }}
                        title={ev.title}
                      >
                        <span className={view === "day" ? "min-w-0 truncate" : "truncate"}>{ev.meetLink ? "📹 " : ""}{ev.title}</span>
                        {view === "day" && <span className="shrink-0 text-[11px] text-text-muted">{ev.allDay ? "All day" : timeOf(new Date(ev.start))}</span>}
                      </button>
                    ))}
                    {view !== "day" && dayEvents.length > (view === "week" ? 4 : 2) && (
                      <div className="text-[9.5px] text-text-faint">+{dayEvents.length - (view === "week" ? 4 : 2)} more</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Upcoming panel */}
        <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
          <h3 className="font-display text-[15px] font-semibold text-text">Upcoming</h3>
          <div className="mt-4 space-y-3">
            {upcoming.length === 0 && <p className="text-[13px] text-text-muted">No upcoming events.</p>}
            {upcoming.map((ev) => {
              const d = new Date(ev.start);
              return (
                <div key={ev.id} className="flex items-start gap-3 rounded-lg border border-border p-3">
                  <span className="mt-1 h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: categoryColor[ev.category] }} />
                  <div className="min-w-0">
                    <div className="truncate text-[13px] font-medium text-text">{ev.title}</div>
                    <div className="mt-0.5 text-[11.5px] text-text-faint">
                      {d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}
                      {!ev.allDay && ` · ${timeOf(d)}`}
                    </div>
                    {ev.meetLink && (
                      <a href={ev.meetLink} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-low hover:underline">
                        <Video size={11} /> Join Meet
                      </a>
                    )}
                    {ev.kind === "event" && <button onClick={() => openEditEvent(ev)} className="mt-2 inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[10px] font-semibold text-text-muted hover:bg-surface-muted"><Pencil size={11} /> Edit</button>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
