import type { NotificationPreferences } from "@/types";

export const NOTIFICATION_PREFERENCES_UPDATED = "nexus-notification-preferences-updated";

export function defaultNotificationPreferences(): NotificationPreferences {
  return {
    dailyBriefEnabled: false,
    dailyBriefTime: "06:00",
    timezone: "Asia/Kolkata",
    deadlineReminders: true,
    highPriorityAlerts: true,
  };
}

export function localNotificationPreferencesKey(ownerId: number) {
  return `nexus-notification-preferences-${ownerId}`;
}

export function readLocalNotificationPreferences(ownerId: number): NotificationPreferences {
  const defaults = defaultNotificationPreferences();
  try {
    const raw = window.localStorage.getItem(localNotificationPreferencesKey(ownerId));
    if (!raw) return defaults;
    const value = JSON.parse(raw) as Partial<NotificationPreferences>;
    const timeValid = typeof value.dailyBriefTime === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value.dailyBriefTime);
    const timezoneValid = typeof value.timezone === "string" && (() => {
      try { new Intl.DateTimeFormat("en", { timeZone: value.timezone }); return true; }
      catch { return false; }
    })();
    return {
      dailyBriefEnabled: typeof value.dailyBriefEnabled === "boolean" ? value.dailyBriefEnabled : defaults.dailyBriefEnabled,
      dailyBriefTime: timeValid ? value.dailyBriefTime! : defaults.dailyBriefTime,
      timezone: timezoneValid ? value.timezone! : defaults.timezone,
      deadlineReminders: typeof value.deadlineReminders === "boolean" ? value.deadlineReminders : defaults.deadlineReminders,
      highPriorityAlerts: typeof value.highPriorityAlerts === "boolean" ? value.highPriorityAlerts : defaults.highPriorityAlerts,
    };
  } catch {
    return defaults;
  }
}

export function writeLocalNotificationPreferences(ownerId: number, preferences: NotificationPreferences) {
  window.localStorage.setItem(localNotificationPreferencesKey(ownerId), JSON.stringify(preferences));
  window.dispatchEvent(new Event(NOTIFICATION_PREFERENCES_UPDATED));
}

export function zonedParts(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "00";
  return {
    day: `${part("year")}-${part("month")}-${part("day")}`,
    minutes: Number(part("hour")) * 60 + Number(part("minute")),
  };
}
