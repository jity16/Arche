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
    <div className="grid overflow-hidden rounded-lg border border-slate-200 bg-white focus-within:border-[#14532d] focus-within:ring-4 focus-within:ring-[#14532d]/10 md:grid-cols-[minmax(0,1fr)_minmax(260px,0.82fr)]">
      <div className="border-b border-slate-200 md:border-b-0 md:border-r md:border-slate-200">
        <div className="flex h-10 items-center justify-between border-b border-slate-100 bg-white px-4">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Input</span>
          <span className="text-[11px] text-slate-400">Markdown / LaTeX</span>
        </div>
        <div className="relative">
          <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            rows={8}
            placeholder={"例如：预测 $\\ce{H2O}$ 在 $\\text{B3LYP/6-31G}^*$ 下的优化几何构型"}
            className="block min-h-56 w-full resize-y border-0 bg-transparent px-4 pb-8 pt-3 font-mono text-sm leading-6 text-slate-900 outline-none placeholder:text-slate-400"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !disabled) onRun?.();
            }}
          />
          <span className="pointer-events-none absolute bottom-2 right-3 rounded bg-white/85 px-1.5 py-0.5 font-mono text-[11px] text-slate-400 ring-1 ring-inset ring-slate-100">
            {value.length} / 8000
          </span>
        </div>
      </div>

      <div className="bg-slate-50/70">
        <div className="flex h-10 items-center justify-between border-b border-slate-100 px-4">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Preview</span>
          <span className="text-[11px] text-slate-400">{value.trim() ? `${value.length} 字符` : "空白"}</span>
        </div>
        <div className="console-scroll min-h-56 overflow-auto px-4 py-3">
        {value.trim() ? (
          <MarkdownText text={value} className="text-sm leading-relaxed text-slate-800" />
        ) : (
          <div className="text-sm text-slate-400">预览将在这里显示</div>
        )}
        </div>
      </div>
    </div>
  );
}
