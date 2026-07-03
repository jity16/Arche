import { FlaskConical, Play, Square } from "lucide-react";
import { MathText } from "../lib/katex";
import { ExpressionEditor } from "./ExpressionEditor";

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
    <div className="w-full max-w-5xl space-y-5">
      <div className="flex justify-center">
        <h1 className="text-center text-2xl font-semibold text-slate-950 sm:text-3xl">
          <span className="question-typewriter" aria-label="今天研究什么">
            今天研究什么
          </span>
        </h1>
      </div>

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_24px_70px_rgba(15,23,42,0.08)]">
        <div className="flex flex-col gap-3 border-b border-slate-200 bg-[#fbfcfb] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-2">
            <FlaskConical className="size-4 text-[#14532d]" />
            <h2 className="text-sm font-semibold text-slate-900">开始新的研究 session</h2>
          </div>
        </div>

        <div className="p-5">
          <ExpressionEditor value={question} onChange={setQuestion} onRun={onRun} disabled={disabled} />

          <div className="mt-5">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">示例任务</div>
              <span className="text-[11px] text-slate-400">点击后填入输入区</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => setQuestion(ex)}
                  className="min-h-11 max-w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-xs leading-relaxed text-slate-600 transition hover:border-[#b7d4c0] hover:bg-[#fbfcfb] hover:text-slate-950"
                >
                  <MathText text={ex} />
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 flex justify-end">
            {running ? (
              <button
                type="button"
                onClick={onStop}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-5 text-sm font-semibold text-rose-700 transition hover:bg-rose-100"
              >
                <Square className="size-3.5" /> 停止运行
              </button>
            ) : (
              <button
                type="button"
                onClick={onRun}
                disabled={disabled}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-[#14532d] px-5 text-sm font-semibold text-white shadow-[0_10px_24px_rgba(20,83,45,0.22)] transition hover:bg-[#166534] disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
              >
                <Play className="size-4 fill-current" /> 开始研究
              </button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
