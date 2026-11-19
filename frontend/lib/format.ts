const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

export const timeOf = (d: Date) =>
  d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true }).toUpperCase();

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Time unavailable";
  const date = new Date(iso);
  return Number.isNaN(date.valueOf()) ? "Time unavailable" : date.toLocaleString("en-IN");
}

export function isTodayDateTime(iso: string | null | undefined, now = new Date()): boolean {
  if (!iso) return false;
  const date = new Date(iso);
  return !Number.isNaN(date.valueOf()) && dateKey(date) === dateKey(now);
}

/** Local YYYY-MM-DD (NOT toISOString, which is UTC and shifts the day in IST) */
export function dateKey(d: Date) {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function formatDeadline(iso: string | null | undefined): string {
  if (!iso) return "No deadline";
  const d = new Date(iso);
  const now = new Date();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const short = d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  if (d.getTime() < now.getTime()) return `Overdue · ${short}`;
  if (sameDay(d, now)) return `Today, ${timeOf(d)}`;
  if (sameDay(d, tomorrow)) return `Tomorrow, ${timeOf(d)}`;
  const days = Math.ceil((d.getTime() - now.getTime()) / 86400000);
  return days <= 7 ? `In ${days} days · ${short}` : `${short}, ${timeOf(d)}`;
}

/** Dynamic deadline label: days until the final 24 hours, then a live hour/minute countdown. */
export function deadlineCountdown(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const remaining = new Date(iso).getTime() - now;
  if (!Number.isFinite(remaining)) return null;
  if (remaining <= 0) {
    const overdueMinutes = Math.floor(Math.abs(remaining) / 60000);
    if (overdueMinutes < 60) return `Overdue by ${overdueMinutes}m`;
    const overdueHours = Math.floor(overdueMinutes / 60);
    if (overdueHours < 24) return `Overdue by ${overdueHours}h ${overdueMinutes % 60}m`;
    return `Overdue by ${Math.floor(overdueHours / 24)}d`;
  }
  if (remaining > 24 * 60 * 60 * 1000) {
    return `${Math.ceil(remaining / (24 * 60 * 60 * 1000))}d left`;
  }
  const totalMinutes = Math.ceil(remaining / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${minutes}m left`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h} hr ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function senderName(from: string) {
  const m = from.match(/^"?([^"<]+?)"?\s*</);
  return (m ? m[1] : from.replace(/<.*>/, "")).trim() || from;
}

export function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export const initialsOf = (name: string) =>
  name.split(" ").filter(Boolean).slice(0, 2).map((n) => n[0]?.toUpperCase()).join("") || "?";
