"use client";

import { useEffect, useState } from "react";
import { deadlineCountdown } from "@/lib/format";

export default function DeadlineCountdown({ deadline, className = "" }: { deadline: string | null | undefined; className?: string }) {
  const [now, setNow] = useState(0);

  useEffect(() => {
    const initial = window.setTimeout(() => setNow(Date.now()), 0);
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, []);

  const label = now ? deadlineCountdown(deadline, now) : null;
  if (!label) return null;
  const overdue = deadline ? new Date(deadline).getTime() <= now : false;
  return <span className={`${overdue ? "text-high" : "text-primary"} ${className}`}>{label}</span>;
}
