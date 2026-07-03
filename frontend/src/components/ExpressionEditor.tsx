import { MarkdownText } from "../lib/katex";

export function ExpressionEditor({
  value,
  onChange,
  onRun,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onRun?: () => void;
  disabled?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 focus-within:border-teal-400 focus-within:bg-white focus-within:ring-4 focus-within:ring-teal-500/10">
      {/* 输入区 */}
      <div className="border-b border-slate-200 bg-white/70">
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          placeholder={"输入 Markdown，可混排行内公式 $...$ 与独立公式 $$...$$。\n例如：**目标**：预测 $\\ce{H2O}$ 在 $\\text{B3LYP/6-31G}^*$ 下的几何构型"}
          className="block w-full resize-y border-0 bg-transparent px-4 py-3 font-mono text-sm text-slate-800 outline-none placeholder:text-slate-400"
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !disabled) onRun?.();
          }}
        />
      </div>

      {/* 实时预览 */}
      <div className="px-4 py-3">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">预览</div>
        {value.trim() ? (
          <MarkdownText text={value} className="text-sm leading-relaxed text-slate-800" />
        ) : (
          <div className="text-sm text-slate-300">Markdown 与公式将在此实时预览...</div>
        )}
      </div>
    </div>
  );
}
