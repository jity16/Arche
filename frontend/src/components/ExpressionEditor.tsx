import { useRef } from "react";
import { MathText } from "../lib/katex";

/** 工具栏按钮。math!==false 时：若光标不在 $...$ 数学区内，插入会自动包裹 $...$，保证预览渲染。 */
type Tool = { label: string; title: string; before: string; after?: string; caretBack?: number; math?: boolean };

const GROUPS: Array<{ name: string; tools: Tool[] }> = [
  {
    name: "数学",
    tools: [
      { label: "$ $", title: "插入行内公式区", before: "$", after: "$", caretBack: 1, math: false },
      { label: "x²", title: "上标", before: "^{", after: "}", caretBack: 1 },
      { label: "xₙ", title: "下标", before: "_{", after: "}", caretBack: 1 },
      { label: "a/b", title: "分式", before: "\\frac{", after: "}{}", caretBack: 2 },
      { label: "√", title: "根号", before: "\\sqrt{", after: "}", caretBack: 1 },
      { label: "∫", title: "积分", before: "\\int_{ }^{ } ", caretBack: 0 },
      { label: "Σ", title: "求和", before: "\\sum_{ }^{ } ", caretBack: 0 },
      { label: "x⃗", title: "矢量", before: "\\vec{", after: "}", caretBack: 1 },
    ],
  },
  {
    name: "希腊",
    tools: [
      { label: "α", title: "alpha", before: "\\alpha " },
      { label: "β", title: "beta", before: "\\beta " },
      { label: "γ", title: "gamma", before: "\\gamma " },
      { label: "Δ", title: "Delta", before: "\\Delta " },
      { label: "θ", title: "theta", before: "\\theta " },
      { label: "λ", title: "lambda", before: "\\lambda " },
      { label: "π", title: "pi", before: "\\pi " },
      { label: "ψ", title: "psi（波函数）", before: "\\psi " },
    ],
  },
  {
    name: "化学",
    tools: [
      { label: "\\ce{}", title: "化学式/方程（mhchem）", before: "\\ce{", after: "}", caretBack: 1 },
      { label: "→", title: "反应箭头", before: "\\ce{ -> }", caretBack: 0 },
      { label: "⇌", title: "可逆反应", before: "\\ce{ <=> }", caretBack: 0 },
      { label: "↑↓", title: "气体/沉淀", before: "\\ce{ ^ }", caretBack: 0 },
      { label: "ΔH", title: "反应焓", before: "\\Delta H_{rxn}" },
    ],
  },
  {
    name: "物理 / 单位",
    tools: [
      { label: "\\pu{}", title: "物理量单位（mhchem）", before: "\\pu{", after: "}", caretBack: 1 },
      { label: "ℏ", title: "约化普朗克常数", before: "\\hbar " },
      { label: "×", title: "乘号", before: "\\times " },
      { label: "·", title: "点乘", before: "\\cdot " },
      { label: "±", title: "正负", before: "\\pm " },
      { label: "°", title: "度", before: "^{\\circ}" },
      { label: "≈", title: "约等于", before: "\\approx " },
    ],
  },
];

/** 粗略判断 pos 处是否在 $...$ 行内数学区（统计前面未转义 $ 的奇偶）。 */
function insideMath(value: string, pos: number): boolean {
  const before = value.slice(0, pos);
  const dollars = (before.match(/\$/g) || []).length;
  return dollars % 2 === 1;
}

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
  const taRef = useRef<HTMLTextAreaElement>(null);

  const insert = (tool: Tool) => {
    const ta = taRef.current;
    const start = ta?.selectionStart ?? value.length;
    const end = ta?.selectionEnd ?? value.length;
    const selected = value.slice(start, end);
    let before = tool.before;
    let after = tool.after ?? "";
    let caretBack = tool.caretBack ?? 0;
    // 数学/化学片段不在 $...$ 内时，自动补上分隔符，保证预览能渲染。
    if (tool.math !== false && !insideMath(value, start)) {
      before = `$${before}`;
      after = `${after}$`;
      caretBack += 1;
    }
    const next = value.slice(0, start) + before + selected + after + value.slice(end);
    onChange(next);
    const caret = start + before.length + selected.length + after.length - caretBack;
    requestAnimationFrame(() => {
      ta?.focus();
      ta?.setSelectionRange(caret, caret);
    });
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 focus-within:border-teal-400 focus-within:bg-white focus-within:ring-4 focus-within:ring-teal-500/10">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-slate-200 px-2.5 py-2">
        {GROUPS.map((group, gi) => (
          <div key={group.name} className="flex items-center gap-1">
            {gi > 0 && <span className="mr-1 h-4 w-px bg-slate-200" />}
            <span className="mr-0.5 select-none text-[10px] font-medium uppercase tracking-wide text-slate-400">
              {group.name}
            </span>
            {group.tools.map((t) => (
              <button
                key={t.label + t.before}
                type="button"
                title={t.title}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => insert(t)}
                className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 font-mono text-xs text-slate-700 transition hover:border-teal-300 hover:bg-teal-50 hover:text-teal-700"
              >
                {t.label}
              </button>
            ))}
          </div>
        ))}
      </div>

      {/* 输入区 */}
      <textarea
        ref={taRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        placeholder={"输入研究问题，可混排自然语言与公式。\n点工具栏按钮即可插入（自动补 $...$）；化学式用 \\ce{...}。\n例如：预测 $\\ce{H2O}$ 在 $\\text{B3LYP/6-31G}^*$ 下的几何构型"}
        className="block w-full resize-y border-0 bg-transparent px-4 py-3 font-mono text-sm text-slate-800 outline-none placeholder:text-slate-400"
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !disabled) onRun?.();
        }}
      />

      {/* 实时预览 */}
      <div className="border-t border-dashed border-slate-200 px-4 py-3">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">预览</div>
        {value.trim() ? (
          <MathText text={value} className="block text-sm leading-relaxed text-slate-800" />
        ) : (
          <div className="text-sm text-slate-300">公式与文字将在此实时渲染…</div>
        )}
      </div>
    </div>
  );
}
