export type Priority = "high" | "medium" | "low";

export type Category = "Academic" | "Administrative" | "Career" | "Informational";

export type SourceType = "gmail" | "notice" | "document" | "calendar" | "college_notices" | "google_classroom" | "manual";

export type TaskStatus = "pending" | "in-progress" | "completed";

export interface Task {
  id: string;
  title: string;
  description: string;
  category: Category;
  priority: Priority;
  deadline: string; // ISO date string
  deadlineLabel: string; // human readable, e.g. "Today, 6:00 PM"
  source: SourceType;
  status: TaskStatus;
  createdAt: string; // ISO date string
  emailSubject?: string | null;
  emailSender?: string | null;
  emailId?: string | null;
  emailThreadId?: string | null;
  emailReceivedAt?: string | null;
}

export interface Source {
  id: string;
  name: string;
  type: SourceType;
  status: "connected" | "active" | "disconnected";
  description: string;
  lastSynced: string | null;
  itemCount: number;
  syncStatus?: string;
  lastSyncError?: string | null;
}

export interface AppNotification {
  id: string;
  title: string;
  description: string;
  time: string;
  read: boolean;
  type: "deadline" | "priority" | "system" | "source" | "daily_brief";
  href?: string;
}

export interface NotificationPreferences {
  dailyBriefEnabled: boolean;
  dailyBriefTime: string;
  timezone: string;
  deadlineReminders: boolean;
  highPriorityAlerts: boolean;
}

export interface ScheduleEvent {
  id: string;
  time: string;
  title: string;
  category: Category;
  taskId?: string;
}

export interface CalendarEvent {
  id: string;
  date: string; // ISO date, day-level
  title: string;
  category: Category;
  priority: string;
  taskId?: string;
}

export interface AppUser {
  id: number;
  name: string;
  email: string;
  picture: string;
  isDemo: boolean;
  lastSyncedAt: string | null;
  gmailLastSyncedAt: string | null;
  calendarLastSyncedAt: string | null;
  externalAiConsent: boolean;
}

/** Google Calendar event, or a task deadline shown on the calendar */
export interface AppEvent {
  id: string;
  title: string;
  start: string; // ISO
  end: string | null;
  allDay: boolean;
  location: string;
  meetLink: string;
  category: Category;
  kind: "event" | "task";
  priority?: Priority;
  taskId?: string;
}

export interface EmailItem {
  id: string;
  sender: string;
  subject: string;
  snippet: string;
  receivedAt: string;
  actionable: boolean;
  taskId: string | null;
  engine: string;
}

export interface Recommendation {
  text: string;
  engine: "gemini" | "rules";
  evidence?: { type: "task" | "calendar"; id: string; title: string; detail: string }[];
}

export interface IntelligenceOverview {
  generatedAt: string;
  dailyDecision?: {
    generatedAt: string;
    calculationVersion: string;
    focus: {
      taskId: string;
      title: string;
      priority: string;
      deadline: string | null;
      deadlineBand: "overdue" | "due_today" | "due_soon" | "upcoming" | "later" | "unknown";
      riskLevel: "high" | "moderate" | "low" | "unknown";
      reason: string;
      source: string;
    } | null;
    priorities: {
      taskId: string;
      title: string;
      priority: string;
      deadline: string | null;
      deadlineBand: "overdue" | "due_today" | "due_soon" | "upcoming" | "later" | "unknown";
      riskLevel: "high" | "moderate" | "low" | "unknown";
      reason: string;
      source: string;
    }[];
    workload: {
      activeTasks: number;
      overdueTasks: number;
      dueTodayTasks: number;
      dueNextSevenDaysTasks: number;
      withoutDeadlines: number;
      estimatedMinutes: number | null;
      availableMinutes: number | null;
      capacityGapMinutes: number | null;
      capacityStatus: "unknown";
      capacityReason: string;
      dependencyStatus: "unavailable";
      blockedTasks: number | null;
      dependencyReason: string;
    };
    evidencePolicy: {
      facts: string[];
      rankingRule: string;
      limitations: string[];
    };
  };
  tasks: {
    pending: number;
    overdue: number;
    dueToday: number;
    dueTomorrow: number;
    upcomingSevenDays: number;
  };
  emails: {
    actionable: number;
    potentialResponseRequests: number;
    unanswered: { status: "unknown"; reason: string };
  };
  calendar: {
    upcomingSevenDays: number;
    conflictCount: number;
    conflicts: { eventIds: string[]; titles: string[]; start: string; end: string }[];
    assessedTimedEvents: number;
    eventsWithoutEnd: number;
  };
  memories: {
    total: number;
    goals: number;
    projects: number;
    highlights: { id: string; category: string; content: string; updatedAt: string }[];
  };
  sources: {
    gmailLastSyncedAt: string | null;
    calendarLastSyncedAt: string | null;
  };
  collegeNotices?: {
    items: { id: string; title: string; summary: string; url: string | null; publishedAt: string | null; checkedAt: string | null }[];
    lastCheckedAt: string | null;
    syncStatus: string;
    newNoticeCount: number;
  };
}

export interface AppConfig {
  demoMode: boolean;
  googleConfigured: boolean;
  ai: "gemini" | "rules";
}

export interface UserMemory {
  id: string;
  kind: "preference" | "fact" | "workflow";
  content: string;
  confidence: string;
  createdAt: string;
  updatedAt: string;
}

export interface ActionSuggestion {
  id: string;
  title: string;
  description: string;
  deadline: string | null;
  priority: string;
  confidence: string;
  emailId: string;
  subject: string;
  sender: string;
  gmailUrl?: string;
  status: string;
  createdAt?: string | null;
}

export interface EmailThreadItem {
  id: string;
  sender: string;
  subject: string;
  receivedAt: string;
  snippet: string;
}

export interface EmailDetail extends EmailThreadItem {
  body: string;
  gmailUrl: string;
  timeline: EmailThreadItem[];
  timelineCoverage: string;
}

export interface MemoryCandidate {
  id: string;
  content: string;
  category: string;
  source: string;
  confidence: string;
  status: string;
  createdAt: string;
}

export interface SystemStatus {
  database: string;
  pids?: {
    enabled: boolean;
    status: string;
    schemaRevision: string | null;
    notificationStorageAvailable: boolean;
    errorCode: string | null;
  };
  externalAi?: { provider: string; configured: boolean; consented: boolean; enabled: boolean };
  inference: {
    provider: string;
    mode: string;
    modelLoaded: boolean;
    offlineReady: boolean;
  };
  voice: { stt: string; tts: string; offlineStt?: boolean; offlineTts?: boolean };
  integrations?: {
    gmail: IntegrationStatus;
    calendar: IntegrationStatus;
    classroom: IntegrationStatus;
    collegeNotices: IntegrationStatus & { googleSessionAvailable: false };
  };
  memoryCount: number;
}

export interface IntegrationStatus {
  configured: boolean;
  requiresInternet: boolean;
  googleSessionAvailable: boolean;
  syncStatus: string;
  lastAttemptAt: string | null;
  lastSuccessAt: string | null;
  errorCode: string | null;
}
