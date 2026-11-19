import { Category, SourceType } from "@/types";
import { Mail, Bell, FileText, Calendar, ListChecks } from "lucide-react";

export const categoryColor: Record<Category, string> = {
  Academic: "#2A56E8",
  Administrative: "#C97A1A",
  Career: "#1E9E6B",
  Informational: "#6B7280",
};

export const categoryBg: Record<Category, string> = {
  Academic: "#EAEFFE",
  Administrative: "#FDF1E1",
  Career: "#E7F7EF",
  Informational: "#F1F3F6",
};

export const sourceMeta: Record<
  SourceType,
  { label: string; icon: typeof Mail; color: string }
> = {
  gmail: { label: "Gmail", icon: Mail, color: "#D64545" },
  notice: { label: "ERP Notice", icon: Bell, color: "#2A56E8" },
  document: { label: "Document", icon: FileText, color: "#1E9E6B" },
  calendar: { label: "Calendar", icon: Calendar, color: "#C97A1A" },
  college_notices: { label: "College notices", icon: Bell, color: "#2A56E8" },
  google_classroom: { label: "Google Classroom", icon: FileText, color: "#1E9E6B" },
  manual: { label: "My task", icon: ListChecks, color: "#1F7A4D" },
};
