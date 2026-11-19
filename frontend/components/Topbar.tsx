"use client";

import { Menu, HelpCircle, Bell } from "lucide-react";
import SearchBar from "./SearchBar";
import { useNexus } from "@/lib/store";
import { initialsOf } from "@/lib/format";
import NotificationPanel from "./NotificationPanel";

interface TopbarProps {
  title: string;
  breadcrumb?: string;
  onOpenMobileMenu: () => void;
}

export default function Topbar({ title, breadcrumb, onOpenMobileMenu }: TopbarProps) {
  const { user } = useNexus();
  return (
    <header className="sticky top-0 z-20 flex h-[72px] items-center gap-4 border-b border-border bg-background/90 px-4 backdrop-blur sm:px-8">
      <button
        onClick={onOpenMobileMenu}
        className="rounded-lg p-2 text-text-muted hover:bg-surface-muted md:hidden"
        aria-label="Open menu"
      >
        <Menu size={20} />
      </button>

      <div className="min-w-0 shrink-0">
        {breadcrumb && (
          <div className="text-[11px] text-text-faint">{breadcrumb}</div>
        )}
        <h1 className="truncate font-display text-[18px] font-semibold text-text">{title}</h1>
      </div>

      <div className="hidden flex-1 justify-center lg:flex">
        <div className="w-full max-w-[360px]"><SearchBar /></div>
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        <button className="hidden rounded-lg p-2 text-text-muted hover:bg-surface-muted hover:text-text sm:block" aria-label="Help">
          <HelpCircle size={19} strokeWidth={1.9} />
        </button>
        <button className="rounded-lg p-2 text-text-muted hover:bg-surface-muted hover:text-text sm:hidden" aria-label="Notifications"><Bell size={18} /></button>
        <NotificationPanel />
        <div className="ml-1 flex h-8 w-8 items-center justify-center rounded-full bg-primary-dim text-xs font-semibold text-primary">
          {initialsOf(user?.name ?? "")}
        </div>
      </div>
    </header>
  );
}
