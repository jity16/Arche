import { Loader2, Square } from "lucide-react";
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
    running ||
    loop.finished ||
    loop.retrieval !== "pending" ||
    loop.hypothesis !== "pending" ||
    loop.rounds.length > 0 ||
    loop.logs.length > 0 ||
    !!result?.timeline?.length;
  const showReport = !!error || (!!result && result.status !== "running");

  return (
    <div className="mx-auto w-full max-w-none space-y-3 pb-6">
      <div className="flex min-h-8 justify-end">
        {running && (
          <button
            type="button"
            onClick={onStop}
            className="inline-flex h-8 w-fit items-center gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-2.5 text-xs font-medium text-rose-700 transition hover:bg-rose-100"
          >
            <Square className="size-3" /> 停止
          </button>
        )}
      </div>

      <section className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-[0_14px_38px_rgba(15,23,42,0.045)]">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">研究问题</div>
        {question ? (
          <MathText text={question} className="mt-1.5 block text-sm font-medium leading-relaxed text-slate-900" />
        ) : (
          <p className="mt-1.5 text-sm font-medium leading-relaxed text-slate-500">—</p>
        )}
      </section>

      {hasLoop ? (
        <AgentLoop state={loop} running={running} result={result} />
      ) : loading && !result ? (
        <div className="flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-10 text-sm text-slate-500">
          <Loader2 className="size-4 animate-spin" /> 正在读取 session…
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
          暂无工作流节点。
        </div>
      )}

      {showReport && (
        <ResultPanel
          result={result}
          error={error}
          showTimeline={false}
          showArtifacts={false}
          showRunningBanner={false}
          showStatusHeader={false}
          showHealthBanner={false}
        />
      )}
    </div>
  );
}
