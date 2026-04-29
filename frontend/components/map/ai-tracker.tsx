"use client";

import { useHudStore } from "@/lib/store/useHudStore";

interface Step {
  label: string;
  sub: string;
}

const STEPS: Step[] = [
  { label: "感知", sub: "分析指令意图" },
  { label: "推理执行", sub: "调用空间工具" },
  { label: "更新画布", sub: "挂载图层结果" },
];

type StepState = "pending" | "active" | "done";

function getStepState(index: number, aiStatus: string): StepState {
  if (aiStatus === "error") {
    // On error, show whatever was completed
    return index === 0 ? "active" : "pending";
  }
  if (aiStatus === "thinking") {
    return index === 0 ? "active" : "pending";
  }
  if (aiStatus === "acting") {
    if (index === 0) return "done";
    if (index === 1) return "active";
    return "pending";
  }
  if (aiStatus === "done") {
    return "done";
  }
  return "pending";
}

const STATE_DOT: Record<StepState, string> = {
  pending: "bg-slate-300",
  active: "bg-blue-500 animate-spulse",
  done: "bg-green-600",
};

export default function AITracker() {
  const aiStatus = useHudStore((s) => s.aiStatus);

  if (aiStatus === "idle") return null;

  return (
    <div
      className="absolute bottom-9 right-3.5 z-45 w-[196px] rounded-xl p-3
                 bg-white/80 backdrop-blur-[20px] border border-white/90
                 shadow-agent-lg animate-fade-up"
    >
      {/* header */}
      <div className="flex items-center gap-1.5 mb-3">
        <span className="w-[6px] h-[6px] rounded-full bg-green-600 animate-spulse" />
        <span className="text-[9px] uppercase text-slate-400 tracking-wider font-medium select-none">
          Agent 运行中
        </span>
      </div>

      {/* steps */}
      <div className="flex flex-col">
        {STEPS.map((step, i) => {
          const state = getStepState(i, aiStatus);
          const isLast = i === STEPS.length - 1;

          return (
            <div key={step.label} className="flex gap-2.5">
              {/* dot + connector column */}
              <div className="flex flex-col items-center">
                <span
                  className={`w-[8px] h-[8px] rounded-full shrink-0 ${STATE_DOT[state]}`}
                />
                {!isLast && (
                  <span className="w-px flex-1 min-h-[16px] bg-slate-200" />
                )}
              </div>

              {/* text */}
              <div className={`pb-3 ${isLast ? "pb-0" : ""}`}>
                <span
                  className={`text-[11px] font-medium leading-tight ${
                    state === "pending"
                      ? "text-slate-400"
                      : state === "active"
                      ? "text-blue-600"
                      : "text-slate-700"
                  }`}
                >
                  {step.label}
                </span>
                <p className="text-[9px] text-slate-400 leading-tight mt-px">
                  {step.sub}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
