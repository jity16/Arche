import { ArrowLeft, Loader2, Square } from "lucide-react";
import { MathText } from "../lib/katex";
import type { RunResult } from "../types";
import { AgentLoop, type LoopState } from "./AgentLoop";
import { ResultPanel } from "./ResultPanel";

/**
 * 独立详情页：一输入问题就进入这里执行;执行中展示实时管线,完成后清晰展示
 * 「研究问题 + 研究结果」。结果由后端持久化(/api/runs),从历史点入同样进入本页回看。
 */
export function DetailView({
  question,
  loop,
  running,
  loading,
  result,
  error,
  onBack,
  onStop,
}: {
  question: string;
  loop: LoopState;
  running: boolean;
  loading: boolean;
  result: RunResult | null;
  error: string | null;
  onBack: () => void;
  onStop: () => void;
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 shadow-sm transition hover:bg-slate-50"
        >
          <ArrowLeft className="size-4" /> 返回
        </button>
        {running && (
          <button
            type="button"
            onClick={onStop}
            className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-medium text-rose-600 transition hover:bg-rose-100"
          >
            <Square className="size-3.5" /> 停止
          </button>
        )}
      </div>

      {/* 研究问题 —— 始终清晰置顶 */}
      <div className="rounded-2xl border border-slate-200 bg-white/85 p-5 shadow-sm shadow-slate-200/40 backdrop-blur">
        <div className="text-xs font-semibold uppercase tracking-wide text-teal-600">研究问题</div>
        {question ? (
          <MathText text={question} className="mt-1.5 block text-base font-medium leading-relaxed text-slate-800" />
        ) : (
          <p className="mt-1.5 text-base font-medium leading-relaxed text-slate-800">—</p>
        )}
      </div>

      {/* 执行中:实时多智能体管线 */}
      {running && (
        <div className="rounded-2xl border border-slate-200 bg-white/85 p-5 shadow-sm shadow-slate-200/40 backdrop-blur">
          <AgentLoop state={loop} running={running} />
        </div>
      )}

      {/* 加载历史记录中 */}
      {loading && !result && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-10 text-sm text-slate-500">
          <Loader2 className="size-4 animate-spin" /> 正在读取记录…
        </div>
      )}

      {/* 执行中且尚无结果 */}
      {running && !result && !error && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
          正在执行多智能体管线,完成后将在此清晰展示研究结果…
        </div>
      )}

      {/* 研究结果(执行完成或历史回看) */}
      {(result || error) && <ResultPanel result={result} error={error} />}
    </div>
  );
}
