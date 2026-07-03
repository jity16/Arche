import { Brain, Check, FlaskConical, Lightbulb, RefreshCw, Search } from "lucide-react";
import type { ComponentType } from "react";

const STAGES: Array<{ key: string; zh: string; en: string; Icon: ComponentType<{ className?: string }> }> = [
  { key: "retrieval", zh: "检索", en: "Retrieval", Icon: Search },
  { key: "hypothesis", zh: "假设", en: "Hypothesis", Icon: Lightbulb },
  { key: "planning", zh: "规划", en: "Planning", Icon: Brain },
  { key: "execution", zh: "执行", en: "Execution", Icon: FlaskConical },
  { key: "reflection", zh: "反思", en: "Reflection", Icon: RefreshCw },
];

export const PIPELINE_STAGE_COUNT = STAGES.length;

/**
 * 工作流执行可视化：activeStage 语义
 *   -1            空闲
 *   0..N-1        正在执行该阶段
 *   N (=5)        全部完成
 */
export function PipelineStages({ activeStage, running }: { activeStage: number; running: boolean }) {
  const done = activeStage >= PIPELINE_STAGE_COUNT;
  const progress = Math.max(0, Math.min(activeStage, PIPELINE_STAGE_COUNT)) / PIPELINE_STAGE_COUNT;

  const caption = (() => {
    if (done) return "工作流执行完成";
    if (running && activeStage >= 0 && activeStage < PIPELINE_STAGE_COUNT) {
      const s = STAGES[activeStage];
      return `正在执行：${s.zh} · ${s.en}…`;
    }
    return "等待提交研究问题";
  })();

  return (
    <div>
      <div className="flex items-start justify-between gap-1 sm:gap-2">
        {STAGES.map((stage, idx) => {
          const isDone = idx < activeStage;
          const isRunning = running && idx === activeStage;
          const node = isDone
            ? "border-[#14532d] bg-[#14532d] text-white"
            : isRunning
              ? "border-amber-400 bg-amber-50 text-amber-700"
              : "border-slate-200 bg-white text-slate-300";
          return (
            <div key={stage.key} className="flex flex-1 items-start">
              <div className="flex flex-1 flex-col items-center gap-1.5 text-center">
                <div className={`relative flex size-11 items-center justify-center rounded-full border-2 transition-colors duration-500 ${node}`}>
                  {isDone ? <Check className="size-5" /> : <stage.Icon className={`size-5 ${isRunning ? "arche-pulse" : ""}`} />}
                  {isRunning && (
                    <span className="absolute inset-[-3px] animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
                  )}
                </div>
                <div className="leading-tight">
                  <div className={`text-xs font-semibold ${isDone ? "text-[#14532d]" : isRunning ? "text-amber-700" : "text-slate-400"}`}>
                    {stage.zh}
                  </div>
                  <div className="text-[10px] uppercase tracking-wide text-slate-300">{stage.en}</div>
                </div>
              </div>
              {idx < STAGES.length - 1 && (
                <div className="mt-5 h-0.5 w-2 shrink-0 overflow-hidden rounded-full bg-slate-200 sm:w-6">
                  <div
                    className="h-full bg-[#14532d] transition-all duration-500"
                    style={{ width: idx < activeStage ? "100%" : "0%" }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 进度条 + 状态说明 */}
      <div className="mt-4 flex items-center gap-3">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-[#14532d] transition-all duration-500" style={{ width: `${progress * 100}%` }} />
        </div>
        <span className={`shrink-0 font-mono text-xs ${done ? "text-[#14532d]" : running ? "text-amber-600" : "text-slate-400"}`}>
          {Math.round(progress * 100)}%
        </span>
      </div>
      <p className={`mt-1.5 text-center text-xs ${running ? "text-amber-600" : done ? "text-[#14532d]" : "text-slate-400"}`}>
        {caption}
      </p>
    </div>
  );
}
