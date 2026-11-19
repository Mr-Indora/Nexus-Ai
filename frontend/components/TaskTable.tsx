"use client";

import { useState } from "react";
import { MoreHorizontal, Check } from "lucide-react";
import { Task } from "@/types";
import PriorityBadge from "./PriorityBadge";
import StatusBadge from "./StatusBadge";
import { categoryColor, categoryBg, sourceMeta } from "@/lib/display";
import { useNexus } from "@/lib/store";
import DeadlineCountdown from "@/components/DeadlineCountdown";
import HoverContextPreview from "@/components/HoverContextPreview";

export default function TaskTable({ tasks }: { tasks: Task[] }) {
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [previewTask, setPreviewTask] = useState<string | null>(null);
  const { setTaskStatus, deleteTask } = useNexus();
  const isDone = (t: Task) => t.status === "completed";
  const toggle = (t: Task) => setTaskStatus(t.id, isDone(t) ? "pending" : "completed");

  if (tasks.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border-strong bg-surface py-16 text-center">
        <p className="text-sm font-medium text-text">No tasks match your filters</p>
        <p className="mt-1 text-xs text-text-muted">Try adjusting search, category or priority filters.</p>
      </div>
    );
  }

  return (
    <>
      {/* Desktop table */}
      <div className="hidden overflow-visible rounded-xl border border-border bg-surface shadow-sm md:block">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-border bg-surface-muted/60 text-[11px] uppercase tracking-wide text-text-faint">
              <th className="w-10 py-3 pl-4"></th>
              <th className="py-3 pr-3 font-medium">Task</th>
              <th className="py-3 pr-3 font-medium">Category</th>
              <th className="py-3 pr-3 font-medium">Source</th>
              <th className="py-3 pr-3 font-medium">Deadline</th>
              <th className="py-3 pr-3 font-medium">Priority</th>
              <th className="py-3 pr-3 font-medium">Status</th>
              <th className="w-10 py-3 pr-4"></th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => {
              const done = isDone(task);
              const SourceIcon = sourceMeta[task.source].icon;
              return (
                <tr id={`task-${task.id}`} key={task.id} className={`relative border-b border-border last:border-b-0 hover:bg-surface-muted/40 ${previewTask === task.id ? "z-20" : ""}`}>
                  <td className="py-3 pl-4">
                    <button
                      onClick={() => toggle(task)}
                      className={`flex h-5 w-5 items-center justify-center rounded-md border transition-colors ${
                        done ? "border-primary bg-primary text-white" : "border-border-strong hover:border-primary"
                      }`}
                    >
                      {done && <Check size={12} strokeWidth={3} />}
                    </button>
                  </td>
                  <td className="max-w-[260px] py-3 pr-3">
                    <HoverContextPreview
                      title={task.title}
                      description={task.description}
                      meta={[task.category, `${task.priority} priority`, task.status.replace("-", " "), task.deadlineLabel, task.emailSender || "", task.emailSubject || ""]}
                      emailId={task.emailId}
                      createdAt={task.createdAt}
                      onOpenChange={(open) => setPreviewTask(open ? task.id : null)}
                    >
                      <div className="cursor-help">
                        <div className={`truncate text-[13.5px] font-medium text-text ${done ? "text-text-faint line-through" : ""}`}>
                          {task.title}
                        </div>
                        <div className="truncate text-[11.5px] text-text-faint">{task.description}</div>
                      </div>
                    </HoverContextPreview>
                  </td>
                  <td className="py-3 pr-3">
                    <span
                      className="whitespace-nowrap rounded-md px-2 py-0.5 text-[11px] font-medium"
                      style={{ color: categoryColor[task.category], backgroundColor: categoryBg[task.category] }}
                    >
                      {task.category}
                    </span>
                  </td>
                  <td className="py-3 pr-3">
                    <span className="flex items-center gap-1.5 whitespace-nowrap text-[12.5px] text-text-muted">
                      <SourceIcon size={13} style={{ color: sourceMeta[task.source].color }} />
                      {sourceMeta[task.source].label}
                    </span>
                  </td>
                  <td className="whitespace-nowrap py-3 pr-3 text-[12.5px] text-text-muted">
                    <div>{task.deadlineLabel}</div>
                    {task.status !== "completed" && task.deadline && <DeadlineCountdown deadline={task.deadline} className="text-[10.5px] font-medium" />}
                  </td>
                  <td className="py-3 pr-3">
                    <PriorityBadge priority={task.priority} />
                  </td>
                  <td className="py-3 pr-3">
                    <StatusBadge status={task.status} />
                  </td>
                  <td className="relative py-3 pr-4 text-right">
                    <button
                      onClick={() => setOpenMenu(openMenu === task.id ? null : task.id)}
                      className="rounded-md p-1 text-text-faint hover:bg-surface-muted hover:text-text"
                    >
                      <MoreHorizontal size={16} />
                    </button>
                    {openMenu === task.id && (
                      <div className="absolute right-4 top-9 z-10 w-36 overflow-hidden rounded-lg border border-border bg-surface py-1 text-left shadow-lg">
                        <button onClick={() => { setTaskStatus(task.id, "in-progress"); setOpenMenu(null); }} className="block w-full px-3 py-1.5 text-[13px] text-text hover:bg-surface-muted">Mark in progress</button>
                        <button onClick={() => { deleteTask(task.id); setOpenMenu(null); }} className="block w-full px-3 py-1.5 text-[13px] text-high hover:bg-high-bg">Delete</button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile cards */}
      <div className="flex flex-col gap-3 md:hidden">
        {tasks.map((task) => {
          const done = isDone(task);
          const SourceIcon = sourceMeta[task.source].icon;
          return (
            <div id={`task-${task.id}`} key={task.id} className="relative rounded-xl border border-border bg-surface p-4 shadow-sm">
              <div className="flex items-start gap-3">
                <button
                  onClick={() => toggle(task)}
                  className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
                    done ? "border-primary bg-primary text-white" : "border-border-strong"
                  }`}
                >
                  {done && <Check size={12} strokeWidth={3} />}
                </button>
                <div className="min-w-0 flex-1">
                  <HoverContextPreview
                    title={task.title}
                    description={task.description}
                    meta={[task.category, `${task.priority} priority`, task.status.replace("-", " "), task.deadlineLabel, task.emailSender || "", task.emailSubject || ""]}
                    emailId={task.emailId}
                    createdAt={task.createdAt}
                  >
                    <div className="cursor-help">
                      <div className={`text-[14px] font-semibold text-text ${done ? "text-text-faint line-through" : ""}`}>
                        {task.title}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-[12px] text-text-muted">{task.description}</p>
                    </div>
                  </HoverContextPreview>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span
                      className="rounded-md px-2 py-0.5 text-[11px] font-medium"
                      style={{ color: categoryColor[task.category], backgroundColor: categoryBg[task.category] }}
                    >
                      {task.category}
                    </span>
                    <PriorityBadge priority={task.priority} />
                    <StatusBadge status={task.status} />
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[11.5px] text-text-faint">
                    <span className="flex items-center gap-1">
                      <SourceIcon size={12} />
                      {sourceMeta[task.source].label}
                    </span>
                    <span>{task.deadlineLabel}</span>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
