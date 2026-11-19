"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle, ArrowUpRight, Bell, BookOpen, CalendarDays, Check, FileText,
  GraduationCap, Landmark, Loader2, LockKeyhole, Mail, Sparkles,
} from "lucide-react";
import { useNexus } from "@/lib/store";

const ERRORS: Record<string, string> = {
  google_not_configured: "Google sign-in is not ready yet. Please ask your workspace owner to check the setup.",
  access_denied: "Google sign-in was cancelled.",
  invalid_state: "Login session expired. Please try again.",
  missing_scopes: "Gmail and Calendar read access are both required. Please allow them on the Google screen.",
  demo_disabled: "Demo mode is disabled on this server.",
};

const sources = [
  { label: "Gmail", icon: Mail, className: "text-[#D85140]" },
  { label: "Calendar", icon: CalendarDays, className: "text-[#4285F4]" },
  { label: "Classroom", icon: GraduationCap, className: "text-[#188038]" },
  { label: "College notices", icon: Landmark, className: "text-[#B7791F]" },
  { label: "LinkedIn", icon: LinkedInMark, className: "text-[#0A66C2]" },
];

export default function LoginPage() {
  const router = useRouter();
  const { status, config } = useNexus();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const e = new URLSearchParams(window.location.search).get("error");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading the URL after hydration
    if (e) setError(ERRORS[e] ?? (e.startsWith("oauth_failed") ? `Google login failed (${e})` : e));
  }, []);

  useEffect(() => {
    if (status === "authed") router.replace("/dashboard");
  }, [status, router]);

  return (
    <main className="min-h-screen bg-[#F5F0E6] lg:grid lg:grid-cols-[minmax(390px,0.84fr)_minmax(0,1.16fr)]">
      <section className="relative order-1 flex min-h-screen flex-col justify-center overflow-hidden bg-[#F5F0E6] px-6 py-10 sm:px-10 lg:px-12 xl:px-20">
        <div className="pointer-events-none absolute -left-28 top-1/4 h-72 w-72 rounded-full bg-[#D6C69D]/15 blur-3xl" />
        <div className="mx-auto w-full max-w-[430px]">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-[#C8B47D]/60 bg-[#173B2B] text-[#E2D3A9] shadow-[0_8px_24px_rgba(20,49,35,0.16)]">
              <Sparkles size={20} strokeWidth={2.2} />
            </div>
            <div>
              <div className="font-display text-lg font-semibold leading-none text-[#1B3024]">Nexus AI</div>
              <div className="mt-1 text-[11px] text-[#7B8379]">Personal Intelligence Workspace</div>
            </div>
          </div>

          <div className="mt-10">
            <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#9C8042]">Your work, composed with clarity</p>
            <h1 className="mt-4 max-w-[410px] font-serif text-[37px] font-medium leading-[1.12] tracking-[-0.035em] text-[#1B3024] sm:text-[43px]">
              One workspace for everything that needs your attention.
            </h1>
            <p className="mt-5 max-w-[390px] text-[14px] leading-6 text-[#637065]">
              Bring email, calendar, study notices and career updates together. See what is coming up, what needs a review and where each update came from.
            </p>
          </div>

          {error && (
            <div role="status" className="mt-6 flex items-start gap-2.5 rounded-xl border border-high/30 bg-high-bg px-3.5 py-3 text-[12.5px] leading-5 text-high">
              <AlertCircle size={16} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="mt-7 space-y-3">
            {status === "loading" ? (
              <div className="flex items-center justify-center gap-2 rounded-xl border border-[#D9D0BE] bg-[#FBF8F1] py-3.5 text-[13px] text-[#637065]">
                <Loader2 size={15} className="animate-spin" /> Checking session…
              </div>
            ) : (
              <>
                <a
                  href="/api/auth/google/login"
                  className="flex w-full items-center justify-center gap-2.5 rounded-xl bg-[#173B2B] py-3.5 text-[14px] font-semibold text-white shadow-[0_9px_24px_rgba(23,59,43,0.18)] transition-colors hover:bg-[#234C38]"
                >
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white">
                    <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true">
                      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                    </svg>
                  </span>
                  Continue with Google
                  <ArrowUpRight size={15} className="ml-1 opacity-75" />
                </a>

                {config?.demoMode && (
                  <>
                    <div className="flex items-center gap-3 py-0.5">
                      <div className="h-px flex-1 bg-[#D9D0BE]" />
                      <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-[#8B9187]">or preview the workspace</span>
                      <div className="h-px flex-1 bg-[#D9D0BE]" />
                    </div>
                    <a
                      href="/api/auth/demo"
                      className="flex w-full items-center justify-center gap-2 rounded-xl border border-[#D7CDB8] bg-[#FBF8F1] py-3 text-[13px] font-medium text-[#27392D] shadow-sm transition-colors hover:bg-white"
                    >
                      <BookOpen size={16} /> Try sample workspace
                    </a>
                  </>
                )}
              </>
            )}
          </div>

          <div className="mt-6 flex items-start gap-2.5 border-t border-[#D7CDB8] pt-4">
            <LockKeyhole size={15} className="mt-0.5 shrink-0 text-[#9C8042]" />
            <p className="text-[11.5px] leading-[1.55] text-[#68746A]">
              Nexus reads Gmail and Calendar to organize your workspace. Classroom is optional. LinkedIn is connected after sign-in from Connections; it is not a separate login method. You stay in control of connected access.
            </p>
          </div>
        </div>
      </section>

      <section className="order-2 relative flex min-h-[610px] items-center overflow-hidden border-t border-[#D7CDB8] bg-[#112119] px-5 py-12 sm:px-10 lg:min-h-screen lg:border-l lg:border-t-0 lg:border-[#31473A] lg:px-10 xl:px-16">
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-[#B79B61]/15 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 left-0 h-80 w-80 rounded-full bg-[#2D5940]/45 blur-3xl" />

        <div className="relative mx-auto w-full max-w-[700px]">
          <div className="mb-5 flex items-center justify-between gap-3">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-[#D7C18A]">One connected view</p>
              <h2 className="mt-2 font-serif text-[24px] font-medium text-[#F4EFDF] sm:text-[29px]">Less searching. Clear next steps.</h2>
            </div>
            <span className="hidden rounded-full border border-[#526456] bg-white/5 px-3 py-1.5 text-[10px] font-medium text-[#C8C7B7] sm:inline-flex">Workspace preview</span>
          </div>

          <div className="overflow-hidden rounded-[22px] border border-[#C7B889]/45 bg-[#F8F4EB] shadow-[0_28px_90px_rgba(0,0,0,0.28)]">
            <div className="flex items-center justify-between border-b border-[#DED7C8] px-4 py-3.5 sm:px-5">
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#173B2B] text-[#E2D3A9]"><Sparkles size={15} /></span>
                <div><p className="text-xs font-semibold text-[#203629]">Nexus workspace</p><p className="text-[10px] text-[#81877C]">Illustrative layout · your records load after sign-in</p></div>
              </div>
              <span className="flex h-8 w-8 items-center justify-center rounded-full border border-[#D7CEBD] text-[#8B8E82]"><Bell size={15} /></span>
            </div>

            <div className="grid min-h-[365px] sm:grid-cols-[150px_minmax(0,1fr)]">
              <aside className="hidden border-r border-[#DED7C8] bg-[#F1ECE1] p-3.5 sm:block">
                <p className="px-2 pb-2 text-[9px] font-semibold uppercase tracking-[0.14em] text-[#8B8E82]">Workspace</p>
                <div className="space-y-1">
                  <PreviewNav icon={Sparkles} label="Overview" active />
                  <PreviewNav icon={Mail} label="Email" />
                  <PreviewNav icon={CalendarDays} label="Calendar" />
                  <PreviewNav icon={GraduationCap} label="Study updates" />
                  <PreviewNav icon={LinkedInMark} label="Career" />
                </div>
              </aside>

              <div className="p-4 sm:p-5">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div><p className="text-[9px] font-semibold tracking-[0.16em] text-[#9C8042]">YOUR DAY, ORGANIZED</p><h3 className="mt-1 font-serif text-[20px] font-medium text-[#203629]">What needs attention</h3></div>
                  <span className="rounded-lg border border-[#D7C18B]/55 bg-[#F2EBD9] px-2.5 py-1.5 text-[10px] font-medium text-[#806A37]">Sources stay linked</span>
                </div>

                <div className="mt-4 space-y-2.5">
                  <PreviewItem icon={Mail} color="text-[#D85140]" eyebrow="EMAIL · REVIEW" title="Important message" detail="Open the original message when you need the full context." />
                  <PreviewItem icon={CalendarDays} color="text-[#4285F4]" eyebrow="CALENDAR · UPCOMING" title="Next event or deadline" detail="See what is coming up and when it is due." />
                  <PreviewItem icon={FileText} color="text-[#B7791F]" eyebrow="STUDY · NEW UPDATE" title="Notice or class update" detail="Follow the source link to read the full announcement." />
                  <PreviewItem icon={LinkedInMark} color="text-[#0A66C2]" eyebrow="CAREER · LINKEDIN" title="Professional source connected separately" detail="Link your LinkedIn profile from Connections after signing in." />
                </div>

                <div className="mt-4 border-t border-border pt-3.5">
                  <p className="mb-2.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-[#8B8E82]">Sources in one workspace</p>
                  <div className="flex flex-wrap gap-2">
                    {sources.map((source) => {
                      const Icon = source.icon;
                      return <span key={source.label} className="inline-flex items-center gap-1.5 rounded-lg border border-[#DED7C8] bg-[#FBF8F1] px-2.5 py-1.5 text-[10px] font-medium text-[#58645A]"><Icon size={12} className={source.className} />{source.label}</span>;
                    })}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-center gap-2 text-[10.5px] text-[#C3C9BB]">
            <Check size={13} className="text-[#D7C18A]" /> Relevant details stay connected to their original source.
          </div>
        </div>
      </section>
    </main>
  );
}

function PreviewNav({ icon: Icon, label, active = false }: { icon: ComponentType<{ size?: number; className?: string }>; label: string; active?: boolean }) {
  return <div className={`flex items-center gap-2 rounded-lg px-2 py-2 text-[10.5px] ${active ? "bg-[#DFE9DC] font-semibold text-[#24543A]" : "text-[#747B70]"}`}><Icon size={13} />{label}</div>;
}

function PreviewItem({ icon: Icon, color, eyebrow, title, detail }: { icon: ComponentType<{ size?: number; className?: string }>; color: string; eyebrow: string; title: string; detail: string }) {
  return <article className="flex gap-3 rounded-xl border border-[#DED7C8] bg-[#FBF8F1] px-3 py-3">
    <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white ${color}`}><Icon size={15} /></span>
    <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1"><p className="text-[9px] font-semibold tracking-[0.08em] text-[#85897D]">{eyebrow}</p><span className="h-1.5 w-8 rounded-full bg-[#D9D2C3]" /></div><p className="mt-1 text-[12px] font-semibold text-[#26392D]">{title}</p><p className="mt-0.5 text-[10px] leading-4 text-[#727B70]">{detail}</p></div>
  </article>;
}

function LinkedInMark({ size = 16, className = "" }: { size?: number; className?: string }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className}><path d="M20.45 2H3.55C2.69 2 2 2.68 2 3.53v16.94c0 .85.69 1.53 1.55 1.53h16.9c.86 0 1.55-.68 1.55-1.53V3.53c0-.85-.69-1.53-1.55-1.53ZM8.13 18.34H5.16V9.5h2.97v8.84ZM6.65 8.29a1.72 1.72 0 1 1 0-3.44 1.72 1.72 0 0 1 0 3.44Zm12.19 10.05h-2.96v-4.3c0-1.03-.02-2.35-1.43-2.35-1.43 0-1.65 1.12-1.65 2.27v4.38H9.84V9.5h2.84v1.21h.04c.4-.7 1.36-1.43 2.8-1.43 3 0 3.56 1.97 3.56 4.53v4.53Z" /></svg>;
}
