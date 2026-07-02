import { FlaskConical, Play, Sparkles } from "lucide-react";
import { ExpressionEditor } from "./ExpressionEditor";
import { AtomSpinner } from "./Logo";

const EXAMPLES = [
  "预测 $\\ce{H2O}$ 在 $\\text{B3LYP/6-31G}^*$ 下的优化几何构型",
  "计算苯 $\\ce{C6H6}$ 的 HOMO–LUMO 能隙 $\\Delta E = E_{LUMO} - E_{HOMO}$",
  "分析 $\\ce{CO2}$ 的振动光谱（IR）吸收峰归属",
  "求反应 $\\ce{N2 + 3H2 <=> 2NH3}$ 的反应焓 $\\Delta H_{rxn}$",
];

export function QuestionForm({
  question,
  setQuestion,
  running,
  disabled,
  onRun,
  onStop,
}: {
  question: string;
  setQuestion: (v: string) => void;
  running: boolean;
  disabled: boolean;
  onRun: () => void;
  onStop: () => void;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white/95 p-5 shadow-sm shadow-slate-200/50 backdrop-blur">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles className="size-4 text-teal-600" />
        <h2 className="text-sm font-semibold text-slate-800">研究问题</h2>
        <span className="text-xs text-slate-400">支持数学 / 化学 / 物理表达式</span>
      </div>

      <ExpressionEditor value={question} onChange={setQuestion} onRun={onRun} disabled={disabled} />

      <div className="mt-3 flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setQuestion(ex)}
            className="max-w-full truncate rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 transition hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700"
          >
            {ex}
          </button>
        ))}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="hidden items-center gap-1.5 text-xs text-slate-400 sm:inline-flex">
          <FlaskConical className="size-3.5" /> ⌘/Ctrl + Enter 快速运行
        </span>
        {running ? (
          <button
            type="button"
            onClick={onStop}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-rose-200 bg-white px-6 py-2.5 text-sm font-semibold text-rose-600 transition hover:bg-rose-50"
          >
            <AtomSpinner className="size-4" /> 停止运行
          </button>
        ) : (
          <button
            type="button"
            onClick={onRun}
            disabled={disabled}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-teal-600 to-cyan-600 px-6 py-2.5 text-sm font-semibold text-white shadow-sm shadow-teal-600/30 transition hover:from-teal-500 hover:to-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Play className="size-4 fill-current" /> 运行工作流
          </button>
        )}
      </div>
    </section>
  );
}
