"use client";

import { useEffect, useRef, useState } from "react";
import { Search, FileText, ListChecks, Plug } from "lucide-react";
import { useNexus } from "@/lib/store";
import type { Task, Source, EmailItem } from "@/types";

interface Result {
  type: "task" | "document" | "source";
  title: string;
  subtitle: string;
}

function getResults(query: string, tasks: Task[], sources: Source[], emails: EmailItem[]): Result[] {
  if (!query.trim()) return [];
  const q = query.toLowerCase();
  const results: Result[] = [];

  tasks
    .filter((t) => t.title.toLowerCase().includes(q) || t.description.toLowerCase().includes(q))
    .slice(0, 4)
    .forEach((t) =>
      results.push({ type: "task", title: t.title, subtitle: `${t.category} · ${t.deadlineLabel}` })
    );

  emails
    .filter((e) => e.subject.toLowerCase().includes(q) || e.sender.toLowerCase().includes(q))
    .slice(0, 2)
    .forEach((e) => results.push({ type: "document", title: e.subject, subtitle: "Email" }));

  sources
    .filter((s) => s.name.toLowerCase().includes(q))
    .forEach((s) => results.push({ type: "source", title: s.name, subtitle: s.status }));

  return results.slice(0, 6);
}

const iconFor = { task: ListChecks, document: FileText, source: Plug };
const labelFor = { task: "TASK", document: "EMAIL", source: "SOURCE" };

export default function SearchBar() {
  const { tasks, sources, emails } = useNexus();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
      if (e.key === "Escape") {
        setOpen(false);
        inputRef.current?.blur();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const results = getResults(query, tasks, sources, emails);

  return (
    <div ref={wrapperRef} className="relative w-full max-w-md">
      <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-muted px-3 py-2 focus-within:border-primary focus-within:bg-surface focus-within:ring-2 focus-within:ring-primary-dim">
        <Search size={16} className="shrink-0 text-text-faint" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          type="text"
          placeholder="Search tasks and emails..."
          className="min-w-0 flex-1 bg-transparent text-sm text-text outline-none placeholder:text-text-faint"
        />
      </div>

      {open && query.trim() && (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 overflow-hidden rounded-xl border border-border bg-surface shadow-lg">
          {results.length > 0 ? (
            <div className="max-h-80 overflow-y-auto py-2">
              <div className="px-3 pb-1 text-[11px] font-medium uppercase tracking-wide text-text-faint">
                Search Results
              </div>
              {results.map((r, i) => {
                const Icon = iconFor[r.type];
                return (
                  <button
                    key={i}
                    className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-surface-muted"
                    onClick={() => setOpen(false)}
                  >
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-dim text-primary">
                      <Icon size={15} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[11px] font-semibold tracking-wide text-text-faint">
                        {labelFor[r.type]}
                      </div>
                      <div className="truncate text-[13px] font-medium text-text">{r.title}</div>
                      <div className="truncate text-[11px] text-text-muted">{r.subtitle}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="px-4 py-6 text-center text-sm text-text-muted">
              No results for &ldquo;{query}&rdquo;
            </div>
          )}
        </div>
      )}
    </div>
  );
}
