import { Loader2, Microscope, Square, UserRound } from "lucide-react";
import { MathText } from "../lib/katex";
import type { RunResult } from "../types";
import { AgentLoop, type LoopState } from "./AgentLoop";
import { ResultPanel } from "./ResultPanel";

/**
 * 会话流：提交问题后不再切换页面，而是在当前 session 中展示用户问题、
 * 实时研究过程与最终结果。历史 session 也复用同一条流。
 */
export function DetailView({
  question,
  loop,
  running,
  loading,
  result,
  error,
  onStop,
}: {
  question: string;
  loop: LoopState;
  running: boolean;
  loading: boolean;
  result: RunResult | null;
  error: string | null;
  onStop: () => void;
}) {
  const hasLoop =
    running || loop.finished || loop.retrieval !== "pending" || loop.hypothesis !== "pending" || loop.rounds.length > 0 || loop.logs.length > 0;
  const cancelled = result?.status === "cancelled" || result?.status === "canceled";

  return (
    <div className="mx-auto max-w-5xl space-y-4 pb-8">
      <div className="flex min-h-9 justify-end">
        {running && (
          <button
            type="button"
            onClick={onStop}
            className="inline-flex h-9 w-fit items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 text-sm font-medium text-rose-700 transition hover:bg-rose-100"
          >
            <Square className="size-3.5" /> 停止
          </button>
        )}
      </div>

      <article className="flex gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm">
          <UserRound className="size-4" />
        </div>
        <div className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white p-4 shadow-[0_18px_50px_rgba(15,23,42,0.05)]">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">研究问题</div>
          {question ? (
            <MathText text={question} className="mt-2 block text-base font-medium leading-relaxed text-slate-900" />
          ) : (
            <p className="mt-2 text-base font-medium leading-relaxed text-slate-500">—</p>
          )}
        </div>
      </article>

      <article className="flex gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[#0b1f17] text-white shadow-[0_10px_24px_rgba(11,31,23,0.18)]">
          <Microscope className="size-4" />
        </div>
        <div className="min-w-0 flex-1 space-y-4">
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.06)]">
            <div className="flex items-center justify-between border-b border-slate-200 bg-[#fbfcfb] px-4 py-3">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[#14532d]">ARCHE Research</div>
                <h3 className="mt-0.5 text-sm font-semibold text-slate-900">研究过程</h3>
              </div>
              <span
                className={`rounded-md px-2 py-1 text-xs font-medium ${
                  running
                    ? "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200"
                    : cancelled
                      ? "bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200"
                    : result
                      ? "bg-[#f3f8f5] text-[#14532d] ring-1 ring-inset ring-[#b7d4c0]"
                      : "bg-slate-100 text-slate-500"
                }`}
              >
                {running ? "研究中" : cancelled ? "已取消" : result ? "已完成" : loading ? "载入中" : "待同步"}
              </span>
            </div>
            <div className="p-4">
              {hasLoop ? (
                <AgentLoop state={loop} running={running} />
              ) : loading && !result ? (
                <div className="flex items-center justify-center gap-2 px-4 py-10 text-sm text-slate-500">
                  <Loader2 className="size-4 animate-spin" /> 正在读取 session…
                </div>
              ) : result ? (
                <p className="text-sm text-slate-500">过程时间线已同步到下方结果报告。</p>
              ) : (
                <p className="text-sm text-slate-500">等待研究过程写入。</p>
              )}
            </div>
          </div>

          {(result || error) && <ResultPanel result={result} error={error} />}
        </div>
      </article>
    </div>
  );
}
