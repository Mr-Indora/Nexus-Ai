"use client";

import { useState } from "react";
import { MoreHorizontal, Check } from "lucide-react";
import { Task } from "@/types";
import PriorityBadge from "./PriorityBadge";
import StatusBadge from "./StatusBadge";
import { categoryColor, categoryBg, sourceMeta } from "@/lib/display";
import { useNexus } from "@/lib/store";

export default function TaskCard({ task }: { task: Task }) {
  const { setTaskStatus, deleteTask } = useNexus();
  const [menuOpen, setMenuOpen] = useState(false);
  const checked = task.status === "completed";
  const SourceIcon = sourceMeta[task.source].icon;

  return (
    <div className="group flex items-start gap-3 rounded-xl border border-border bg-surface p-4 shadow-sm transition-colors hover:border-border-strong">
      <button
        onClick={() => setTaskStatus(task.id, checked ? "pending" : "completed")}
        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${
          checked ? "border-primary bg-primary text-white" : "border-border-strong hover:border-primary"
        }`}
        aria-label="Toggle complete"
      >
        {checked && <Check size={13} strokeWidth={3} />}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className={`text-[14px] font-semibold text-text ${checked ? "line-through text-text-faint" : ""}`}>
            {task.title}
          </h3>
          <div className="relative shrink-0">
            <button
              onClick={() => setMenuOpen((o) => !o)}
              className="rounded-md p-1 text-text-faint opacity-0 hover:bg-surface-muted hover:text-text group-hover:opacity-100"
              aria-label="More options"
            >
              <MoreHorizontal size={17} />
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-8 z-10 w-40 overflow-hidden rounded-lg border border-border bg-surface py-1 shadow-lg">
                <button
                  onClick={() => { setTaskStatus(task.id, "in-progress"); setMenuOpen(false); }}
                  className="block w-full px-3 py-1.5 text-left text-[13px] text-text hover:bg-surface-muted"
                >
                  Mark in progress
                </button>
                <button
                  onClick={() => { deleteTask(task.id); setMenuOpen(false); }}
                  className="block w-full px-3 py-1.5 text-left text-[13px] text-high hover:bg-high-bg"
                >
                  Delete
                </button>
              </div>
            )}
          </div>
        </div>

        <p className="mt-1 line-clamp-1 text-[12.5px] text-text-muted">{task.description}</p>

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <span
            className="rounded-md px-2 py-0.5 text-[11px] font-medium"
            style={{ color: categoryColor[task.category], backgroundColor: categoryBg[task.category] }}
          >
            {task.category}
          </span>
          <PriorityBadge priority={task.priority} />
          <StatusBadge status={task.status} />
          <span className="text-[11.5px] font-medium text-text-muted">{task.deadlineLabel}</span>
          <span className="ml-auto flex items-center gap-1 text-[11px] text-text-faint">
            <SourceIcon size={12} />
            {sourceMeta[task.source].label}
          </span>
        </div>
      </div>
    </div>
  );
}
