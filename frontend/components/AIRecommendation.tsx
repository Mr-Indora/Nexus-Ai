import { Sparkles } from "lucide-react";
import { Recommendation } from "@/types";

export default function AIRecommendation({ rec }: { rec: Recommendation | null }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-primary/20 bg-primary-dim/60 p-4 sm:p-5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-white">
        <Sparkles size={17} />
      </div>
      <div className="min-w-0">
        <h3 className="font-display text-[14px] font-semibold text-text">Next best action</h3>
        <p className="mt-1 text-[13.5px] leading-relaxed text-text">
          {rec ? rec.text : "Analyzing your tasks and schedule…"}
        </p>
        {!!rec?.evidence?.length && (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {rec.evidence?.map((item) => (
              <li key={`${item.type}-${item.id}`} className="text-[11px] text-text-faint">
                {item.type === "task" ? "Task" : "Calendar"}: {item.title} · {item.detail}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
