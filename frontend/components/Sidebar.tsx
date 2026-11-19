"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useNexus } from "@/lib/store";
import { initialsOf } from "@/lib/format";
import {
  LayoutDashboard,
  ListChecks,
  Mail,
  Calendar,
  Settings,
  BriefcaseBusiness,
  ChevronsLeft,
  ChevronsRight,
  LogOut,
  Sparkles,
  X,
} from "lucide-react";

const navItems = [
  { href: "/dashboard", label: "Home", icon: LayoutDashboard },
  { href: "/tasks", label: "Tasks & Goals", icon: ListChecks },
  { href: "/intelligence", label: "Insights", icon: Mail },
  { href: "/sources", label: "Connections", icon: Mail },
  { href: "/linkedin", label: "LinkedIn", icon: BriefcaseBusiness },
  { href: "/calendar", label: "Calendar", icon: Calendar },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export default function Sidebar({
  collapsed,
  onToggleCollapse,
  mobileOpen,
  onCloseMobile,
}: SidebarProps) {
  const pathname = usePathname();
  const { user, logout } = useNexus();

  const content = (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div className={`flex items-center gap-2.5 px-4 pt-5 pb-5 ${collapsed ? "justify-center px-2" : ""}`}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-[0_0_24px_rgba(31,122,77,0.2)]">
          <Sparkles size={18} strokeWidth={2.25} />
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="font-display text-[15px] font-semibold leading-none text-text">
              Nexus AI
            </div>
            <div className="mt-1 text-[11px] leading-none text-text-faint">
              Personal Intelligence Workspace
            </div>
          </div>
        )}
        <button
          onClick={onCloseMobile}
          className="ml-auto rounded-md p-1 text-text-muted hover:bg-surface-muted md:hidden"
          aria-label="Close menu"
        >
          <X size={18} />
        </button>
      </div>

      {/* Nav */}
      <div className={!collapsed ? "px-4 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-text-faint" : "sr-only"}>Workspace</div>
      <nav className="flex-1 space-y-0.5 px-2.5 py-2">
        {navItems.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.label}
              href={item.href}
              onClick={onCloseMobile}
              title={collapsed ? item.label : undefined}
              className={`group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13.5px] font-medium transition-colors ${
                collapsed ? "justify-center px-0" : ""
              } ${
                active
                  ? "bg-primary-dim text-primary"
                  : "text-text-muted hover:bg-surface-muted hover:text-text"
              }`}
            >
              {active && !collapsed && (
                <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r-full bg-primary" />
              )}
              <Icon size={18} strokeWidth={2} className="shrink-0" />
              {!collapsed && <span className="flex-1">{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Sidebar edge toggle stays easy to find in both expanded and compact modes. */}
      <button onClick={onToggleCollapse} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="absolute -right-3 top-1/2 z-20 hidden h-10 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-surface text-text-muted shadow-sm hover:text-primary md:flex">
        {collapsed ? <ChevronsRight size={15}/> : <ChevronsLeft size={15}/>}
      </button>

      {/* User */}
      <div className={`border-t border-border p-3 ${collapsed ? "flex justify-center" : ""}`}>
        <div className={`flex items-center gap-2.5 rounded-lg p-1.5 ${collapsed ? "" : "hover:bg-surface-muted"}`}>
          {user?.picture ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={user.picture} alt="" referrerPolicy="no-referrer" className="h-8 w-8 shrink-0 rounded-full object-cover" />
          ) : (
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-dim text-xs font-semibold text-primary">
              {initialsOf(user?.name ?? "")}
            </div>
          )}
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-semibold text-text">{user?.name}</div>
              <div className="truncate text-[11px] text-text-faint">{user?.email}</div>
            </div>
          )}
          {!collapsed && (
            <button onClick={logout} className="rounded-md p-1.5 text-text-faint hover:bg-white hover:text-high" aria-label="Logout">
              <LogOut size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <aside
        className={`sticky top-0 hidden h-screen shrink-0 border-r border-border bg-surface transition-all duration-200 md:block relative ${
          collapsed ? "w-[76px]" : "w-64"
        }`}
      >
        {content}
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/30" onClick={onCloseMobile} />
          <aside className="absolute left-0 top-0 h-full w-64 bg-surface shadow-xl">
            {content}
          </aside>
        </div>
      )}
    </>
  );
}
