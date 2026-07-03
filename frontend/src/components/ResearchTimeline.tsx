import {
  AlertTriangle,
  Brain,
  Check,
  ChevronDown,
  Download,
  Eye,
  FileJson,
  FileText,
  FlaskConical,
  Hourglass,
  Lightbulb,
  Loader2,
  PanelRightOpen,
  RefreshCw,
  Search,
  XCircle,
} from "lucide-react";
import { type ComponentType, type ReactNode, useEffect, useState } from "react";
import { archeApi } from "../api";
import { MathText } from "../lib/katex";
import type { ArtifactFile, TimelineStep } from "../types";

/** 把 controller 的 step 名映射成可读标签 + 图标；reflection_phase_round_N 提取轮次。 */
export function stepInfo(step: string): { label: string; Icon: ComponentType<{ className?: string }> } {
  const mRound = step.match(/reflection_phase_round_(\d+)/);
  if (mRound) return { label: `第 ${mRound[1]} 轮 · 反思`, Icon: RefreshCw };
  switch (step) {
    case "retrieval_phase":
      return { label: "文献检索", Icon: Search };
    case "hypothesis_phase":
      return { label: "假设生成", Icon: Lightbulb };
    case "planner_phase":
      return { label: "计算规划", Icon: Brain };
    case "execution_phase":
      return { label: "工具执行", Icon: FlaskConical };
    default:
      return { label: step, Icon: FlaskConical };
  }
}

/** 每个 step 完成时 controller 写的分阶段 JSON 产物文件名（已被持久化、可下钻拉取全文）。 */
export function stepArtifactName(step: string): string | null {
  const m = step.match(/reflection_phase_round_(\d+)/);
  if (m) return `reflection_result_round_${m[1]}.json`;
  const map: Record<string, string> = {
    retrieval_phase: "retrieval_result.json",
    hypothesis_phase: "hypothesis_result.json",
    planner_phase: "planner_result.json",
    execution_phase: "execution_result.json",
  };
  return map[step] || null;
}

const STATUS_TONE: Record<string, { dot: string; badge: string; text: string }> = {
  started: { dot: "bg-amber-400", badge: "bg-amber-50 text-amber-700 ring-amber-500/20", text: "进行中" },
  completed: { dot: "bg-emerald-500", badge: "bg-emerald-50 text-emerald-700 ring-emerald-500/20", text: "完成" },
  failed: { dot: "bg-rose-500", badge: "bg-rose-50 text-rose-700 ring-rose-500/20", text: "失败" },
  waiting_for_gaussian_jobs: { dot: "bg-amber-400", badge: "bg-amber-50 text-amber-700 ring-amber-500/20", text: "等待 Gaussian" },
};

// data/JSON 键 → 中文标签；未列出回退原名。
const KEY_LABELS: Record<string, string> = {
  keywords: "关键词", index_built: "已建检索索引", has_literature_review: "含文献综述",
  total_queries: "查询数", total_hypotheses: "假设数", optimized_hypotheses: "优化后假设",
  ranked_strategies: "排名策略", top_n_strategies: "候选策略", workflow_version: "流程版本",
  total_protocols: "计算方案数", total_original_steps: "原始步骤", total_optimized_steps: "优化后步骤",
  optimization_ratio: "优化比", used_expert_backend: "专家模型", fallback_triggered: "触发降级",
  total_workflows: "工作流数", total_steps: "总步骤", successful_steps: "成功步骤",
  overall_success_rate: "成功率", decision: "决策", reason: "原因", reasoning: "推理",
  confidence: "置信度", error: "错误", jobs: "Gaussian 任务", count: "数量",
  literature_review: "文献综述", mechanistic_clues: "机理线索", chemistry_context: "化学上下文",
  detailed_reasoning: "详细推理", strategy: "策略", strategy_name: "策略名", hypothesis: "假设",
  hypotheses: "假设", hypotheses_by_query: "各查询假设", optimized_protocols: "计算方案",
  Steps: "步骤", steps: "步骤", tool_name: "工具", parameters: "参数", description: "说明",
  raw_output: "原始输出", parsed_results: "解析结果", energy: "能量", frequencies: "频率",
  gaussian_analysis: "Gaussian 分析", expert_error_analysis: "错误分析", identified_problems: "发现的问题",
  recommended_actions: "建议动作", workflow_revision_instructions: "工作流修订指令",
  hypothesis_revision_instructions: "假设修订指令", results: "结果", query: "查询", queries: "查询",
  status: "状态", gaussian_review_summary: "Gaussian 专家审阅", expected_validation_requirements: "验证要求",
  // 化学上下文
  solvent: "溶剂", method: "计算方法", basis_set: "基组", functional: "泛函", temperature: "温度",
  charge: "电荷", multiplicity: "自旋多重度", spin_multiplicity: "自旋多重度", level_of_theory: "理论水平",
  software: "软件", reaction_type: "反应类型", substrate: "底物", solvation_model: "溶剂化模型",
  // 执行 / 工作流
  step_name: "步骤", step_id: "步骤编号", error_info: "错误信息", summary: "概要", issues: "问题",
  overall_status: "总体状态", workflow_outcome: "工作流结论", validation_overview: "验证概览",
  // 结论
  conclusion_summary: "结论摘要", conclusion_type: "结论类型", evidence_summary: "证据概要",
};
function prettyKey(k: string): string {
  return KEY_LABELS[k] || k;
}

// 状态枚举 → 中文（执行步骤/工作流的 status / overall_status）。
const STATUS_VALUE_LABEL: Record<string, string> = {
  success: "成功", successful: "成功", completed: "完成", done: "完成", passed: "通过",
  failed: "失败", failure: "失败", error: "出错", pending: "待执行", running: "进行中",
  partial_success: "部分成功", partial: "部分成功", skipped: "跳过", timeout: "超时",
  aborted: "中止", cancelled: "已取消", canceled: "已取消", waiting: "等待", not_run: "未执行", normal_termination: "正常结束",
};
// 工具名 → 中文（toolpool 真实工具）。
const TOOL_LABEL: Record<string, string> = {
  smiles2sdf: "SMILES→3D结构(SDF)", sdf_to_gjf: "SDF→Gaussian输入", sdf2gjf: "SDF→Gaussian输入",
  xyz_to_gjf: "XYZ→Gaussian输入", xyz2gjf: "XYZ→Gaussian输入", gen_conformation: "构象生成",
  generate_gaussian_code: "生成Gaussian脚本", gen_gaussiancode: "生成Gaussian脚本", run_gaussian: "运行Gaussian",
  plot_tools: "光谱绘图", plot_spectrum: "光谱绘图", process_spectrum: "光谱处理", Multiwfn: "Multiwfn波函数分析",
};
function toolLabel(name: string): string {
  return TOOL_LABEL[name] || name;
}
// 指标释义（hover 悬停说明，内联预览与下钻通用）。
const KEY_HINT: Record<string, string> = {
  optimization_ratio: "优化比 = 优化后步骤数 ÷ 原始步骤数；越小表示步骤被精简得越多",
  overall_success_rate: "成功率 = 成功完成的步骤数 ÷ 总步骤数",
  confidence: "置信度：智能体对该结果/决策的自评可信程度（0–100%，越高越有把握）",
  total_optimized_steps: "规划优化后保留的计算步骤数",
  total_original_steps: "规划优化前的原始计算步骤数",
};

const HIDDEN_KEYS = new Set(["output_file", "protocols_file", "_omitted"]);
const DECISION_LABEL: Record<string, string> = {
  accept: "采纳", stop: "停止", revise_workflow: "修订工作流", revise_hypothesis: "修订假设", reflection_error: "反思出错",
};

function fmtScalar(key: string, v: unknown): string {
  if (typeof v === "boolean") return v ? "是" : "否";
  if (key === "decision" && typeof v === "string") return DECISION_LABEL[v] || v;
  if ((key === "status" || key === "overall_status") && typeof v === "string") return STATUS_VALUE_LABEL[v] || v;
  if ((key === "overall_success_rate" || key === "optimization_ratio") && (typeof v === "number" || typeof v === "string")) {
    const n = Number(v);
    if (Number.isFinite(n) && n <= 1) return `${(n * 100).toFixed(0)}%`;
  }
  return typeof v === "number" || typeof v === "string" ? String(v) : JSON.stringify(v);
}

/** 长文本截断 + 展开（含化学公式 MathText 渲染）。 */
function LongText({ text }: { text: string }) {
  const [exp, setExp] = useState(false);
  const long = text.length > 360;
  const shown = exp || !long ? text : `${text.slice(0, 360)}…`;
  return (
    <span className="text-[11px] leading-relaxed text-slate-700">
      <MathText text={shown} className="inline" />
      {long && (
        <button type="button" onClick={() => setExp((v) => !v)} className="ml-1 text-[#14532d] hover:underline">
          {exp ? "收起" : "展开"}
        </button>
      )}
    </span>
  );
}

/** 稳健的可读 JSON 渲染：不猜结构、按层级递归（深度/条数有上限），字符串走 MathText、布尔中文化、
 *  键名中文标签化 —— 把任意分阶段 JSON 产物渲染成可读内容，完整原文仍可在产物区下载。 */
function JsonView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="text-slate-300">—</span>;
  if (typeof value === "string") return <LongText text={value} />;
  if (typeof value === "number") return <span className="font-mono text-slate-700">{String(value)}</span>;
  if (typeof value === "boolean") return <span className="font-mono text-slate-700">{value ? "是" : "否"}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-slate-300">（空）</span>;
    return (
      <div className="space-y-1">
        {value.slice(0, 30).map((item, i) => (
          <div key={`i${i}`} className="flex gap-1.5">
            <span className="mt-0.5 shrink-0 font-mono text-[10px] text-slate-300">{i + 1}.</span>
            <div className="min-w-0 flex-1">
              <JsonView value={item} depth={depth + 1} />
            </div>
          </div>
        ))}
        {value.length > 30 && <div className="text-[10px] text-slate-400">… 其余 {value.length - 30} 项（见下载文件）</div>}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>).filter(
    ([k, v]) => !HIDDEN_KEYS.has(k) && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0),
  );
  if (entries.length === 0) return <span className="text-slate-300">（空）</span>;
  if (depth >= 5) return <span className="text-[10px] text-slate-400">（更深层级见下载文件）</span>;
  return (
    <div className="space-y-1">
      {entries.slice(0, 40).map(([k, v]) => {
        const scalar = typeof v !== "object" || v === null;
        return (
          <div key={k} className={scalar ? "flex flex-wrap items-baseline gap-1.5" : ""}>
            <span className="shrink-0 text-[11px] font-medium text-slate-500">
              {prettyKey(k)}
              {scalar ? "：" : ""}
            </span>
            {scalar ? (
              <span className="min-w-0 text-[11px]">
                <JsonView value={fmtScalar(k, v) === String(v) ? v : fmtScalar(k, v)} depth={depth + 1} />
              </span>
            ) : (
              <div className="ml-2 mt-0.5 border-l border-slate-100 pl-2">
                <JsonView value={v} depth={depth + 1} />
              </div>
            )}
          </div>
        );
      })}
      {entries.length > 40 && <div className="text-[10px] text-slate-400">… 其余字段见下载文件</div>}
    </div>
  );
}

// ============ 分步骤定制可视化（替代通用 JsonView 下钻，让每步详情可读）============
// 取值小工具：产物字段随 run 变化、可能缺失，统一安全取用、缺失即跳过渲染。
function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function asArr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function asStr(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : typeof v === "object" ? "" : String(v);
}
/** 从可能是字符串或 {text/field:...} 对象的条目里取一段可读文本。 */
function itemText(v: unknown, ...fields: string[]): string {
  if (typeof v === "string") return v;
  const o = asObj(v);
  for (const f of fields) if (asStr(o[f])) return asStr(o[f]);
  return Object.values(o).map(asStr).filter(Boolean)[0] || "";
}

function Sub({ children }: { children: ReactNode }) {
  return <div className="mb-1 mt-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">{children}</div>;
}
/** 置信度（0–1 或 0–100）→ 进度条 + 百分比。 */
function ConfidenceBar({ value }: { value: unknown }) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  const pct = Math.max(0, Math.min(100, n <= 1 ? n * 100 : n));
  return (
    <span className="inline-flex items-center gap-1" title="置信度：智能体对该结果/决策的自评可信程度（0–100%，越高越有把握）">
      <span className="block h-1.5 w-14 overflow-hidden rounded-full bg-slate-100">
        <span className="block h-full rounded-full bg-[#14532d]" style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-[10px] text-slate-500">{pct.toFixed(0)}%</span>
    </span>
  );
}

function RetrievalView({ d }: { d: Record<string, unknown> }) {
  const kws = asArr(d.keywords);
  const ctx = Object.entries(asObj(d.chemistry_context)).filter(([, v]) => v != null && v !== "");
  const clues = asArr(d.mechanistic_clues);
  const lims = asArr(d.limitations);
  const review = asStr(d.literature_review);
  return (
    <div className="space-y-2 text-[11px]">
      {kws.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {kws.map((k) => (
            <span key={asStr(k)} className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-700 ring-1 ring-inset ring-slate-200">
              {asStr(k)}
            </span>
          ))}
        </div>
      )}
      {ctx.length > 0 && (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg bg-slate-50 px-2.5 py-2">
          {ctx.map(([k, v]) => (
            <div key={k} className="flex gap-1">
              <span className="shrink-0 text-slate-400">{prettyKey(k)}：</span>
              <span className="min-w-0 text-slate-700"><MathText text={asStr(v)} className="inline" /></span>
            </div>
          ))}
        </div>
      )}
      {clues.length > 0 && (
        <div>
          <Sub>机理线索</Sub>
          <ol className="list-decimal space-y-0.5 pl-4 text-slate-700">
            {clues.map((c, i) => (
              <li key={i}><MathText text={itemText(c, "clue", "text")} className="inline" /></li>
            ))}
          </ol>
        </div>
      )}
      {lims.length > 0 && (
        <div>
          <Sub>局限</Sub>
          <ul className="list-disc space-y-0.5 pl-4 text-slate-500">
            {lims.map((c, i) => (
              <li key={i}><MathText text={itemText(c, "limitation", "text")} className="inline" /></li>
            ))}
          </ul>
        </div>
      )}
      {review && (
        <div>
          <Sub>文献综述</Sub>
          <div className="console-scroll max-h-56 overflow-auto rounded-lg border border-slate-100 bg-white px-2.5 py-2 leading-relaxed text-slate-700">
            <MathText text={review} />
          </div>
        </div>
      )}
    </div>
  );
}

function HypothesisView({ d }: { d: Record<string, unknown> }) {
  const ranked = asArr(d.ranked_strategies);
  const byQuery = asArr(d.hypotheses_by_query);
  return (
    <div className="space-y-2 text-[11px]">
      {ranked.length > 0 && (
        <div className="space-y-1.5">
          {ranked.map((s, i) => {
            const o = asObj(s);
            return (
              <div key={i} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 font-semibold text-slate-700">
                    <span className="flex size-4 items-center justify-center rounded-full bg-amber-100 text-[9px] font-bold text-amber-700">{i + 1}</span>
                    {asStr(o.strategy_name) || `策略 ${i + 1}`}
                  </span>
                  <ConfidenceBar value={o.confidence} />
                </div>
                {asStr(o.hypothesis) && <div className="mt-1 text-slate-700"><MathText text={asStr(o.hypothesis)} className="inline" /></div>}
                {asStr(o.detailed_reasoning) && (
                  <div className="mt-1 border-l-2 border-slate-100 pl-2 text-slate-500"><LongText text={asStr(o.detailed_reasoning)} /></div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {byQuery.length > 0 && (
        <div>
          <Sub>各查询的假设</Sub>
          <div className="space-y-1">
            {byQuery.map((q, i) => {
              const o = asObj(q);
              const hs = asArr(o.hypotheses);
              return (
                <div key={i} className="rounded-lg bg-slate-50 px-2.5 py-1.5">
                  <div className="font-medium text-slate-600"><MathText text={asStr(o.query)} className="inline" /></div>
                  {hs.length > 0 && (
                    <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-slate-600">
                      {hs.slice(0, 8).map((h, j) => (
                        <li key={j}><MathText text={itemText(h, "hypothesis", "text")} className="inline" /></li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function PlannerView({ d }: { d: Record<string, unknown> }) {
  const protocols = asArr(d.optimized_protocols);
  const orig = d.total_original_steps;
  const opt = d.total_optimized_steps;
  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2">
        {(orig != null || opt != null) && (
          <span className="text-slate-600">
            步骤优化：<span className="font-mono">{asStr(orig) || "?"}</span> → <span className="font-mono font-semibold text-[#14532d]">{asStr(opt) || "?"}</span>
          </span>
        )}
        {d.optimization_ratio != null && (
          <span className="rounded bg-[#eef7f1] px-1.5 py-0.5 text-[10px] text-[#14532d]" title="优化比 = 优化后步骤数 ÷ 原始步骤数；越小表示步骤被精简得越多">优化比 {fmtScalar("optimization_ratio", d.optimization_ratio)}</span>
        )}
        {d.fallback_triggered === true && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-medium text-rose-600">已触发降级</span>}
      </div>
      {protocols.map((p, pi) => {
        const o = asObj(p);
        const steps = asArr(o.Steps ?? o.steps);
        return (
          <div key={pi} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2">
            {protocols.length > 1 && <div className="mb-1 font-semibold text-slate-600">方案 {pi + 1}</div>}
            <ol className="space-y-1">
              {steps.map((st, si) => {
                const s = asObj(st);
                const pe = Object.entries(asObj(s.parameters)).filter(([, v]) => v != null && v !== "").slice(0, 6);
                return (
                  <li key={si} className="flex gap-2">
                    <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[9px] font-bold text-slate-500">{si + 1}</span>
                    <div className="min-w-0 flex-1">
                      <span className="font-medium text-slate-700" title={asStr(s.tool_name)}>{toolLabel(asStr(s.tool_name)) || "步骤"}</span>
                      {asStr(s.description) && <span className="ml-1 text-slate-500"><MathText text={asStr(s.description)} className="inline" /></span>}
                      {pe.length > 0 && (
                        <div className="mt-0.5 flex flex-wrap gap-1">
                          {pe.map(([k, v]) => (
                            <span key={k} className="rounded bg-slate-50 px-1.5 py-0.5 font-mono text-[9px] text-slate-500">{k}={asStr(v)}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        );
      })}
    </div>
  );
}

function ExecStep({ step }: { step: Record<string, unknown> }) {
  const [raw, setRaw] = useState(false);
  const st = asStr(step.status).toLowerCase();
  const ok = st.includes("success") || st === "completed" || st === "done";
  const failed = st.includes("fail") || st.includes("error");
  const parsed = asObj(step.parsed_results);
  const ga = asObj(step.gaussian_analysis);
  const energy = parsed.energy ?? ga.energy;
  const freqs = asArr(parsed.frequencies ?? ga.frequencies);
  const rawOut = asStr(step.raw_output);
  return (
    <div className="mt-1 border-l-2 border-slate-100 pl-2">
      <div className="flex items-center gap-1.5">
        {ok ? <Check className="size-3 shrink-0 text-emerald-500" /> : failed ? <XCircle className="size-3 shrink-0 text-rose-500" /> : <Hourglass className="size-3 shrink-0 text-amber-500" />}
        <span className="font-medium text-slate-600">{asStr(step.step_name) || toolLabel(asStr(step.tool_name)) || "步骤"}</span>
        {asStr(step.tool_name) && asStr(step.step_name) && <span className="font-mono text-[9px] text-slate-300" title={asStr(step.tool_name)}>{toolLabel(asStr(step.tool_name))}</span>}
      </div>
      {(energy != null || freqs.length > 0) && (
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-slate-600">
          {energy != null && <span>能量 {asStr(energy)}</span>}
          {freqs.length > 0 && <span>频率 {freqs.slice(0, 4).map(asStr).join(", ")}{freqs.length > 4 ? "…" : ""}</span>}
        </div>
      )}
      {asStr(step.error_info) && <div className="mt-0.5 text-[10px] text-rose-500"><MathText text={asStr(step.error_info)} className="inline" /></div>}
      {rawOut && (
        <div className="mt-0.5">
          <button type="button" onClick={() => setRaw((v) => !v)} className="text-[9px] text-slate-400 hover:text-slate-600">{raw ? "收起原始输出" : "原始输出"}</button>
          {raw && <pre className="console-scroll mt-0.5 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-slate-900 px-2 py-1 font-mono text-[9px] leading-relaxed text-slate-300">{rawOut}</pre>}
        </div>
      )}
    </div>
  );
}

function ExecutionView({ d }: { d: Record<string, unknown> }) {
  const results = asArr(d.results);
  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-2">
        {d.successful_steps != null && d.total_steps != null && (
          <span className="text-slate-600">成功步骤 <span className="font-semibold text-emerald-700">{asStr(d.successful_steps)}</span>/<span className="font-mono">{asStr(d.total_steps)}</span></span>
        )}
        {d.overall_success_rate != null && (
          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700" title="成功率 = 成功完成的步骤数 ÷ 总步骤数">成功率 {fmtScalar("overall_success_rate", d.overall_success_rate)}</span>
        )}
      </div>
      {results.map((wf, wi) => {
        const o = asObj(wf);
        const ws = asStr(o.overall_status).toLowerCase();
        const wfok = ws.includes("success") || ws === "completed";
        const issues = asArr(o.issues);
        return (
          <div key={wi} className="rounded-lg border border-slate-200 bg-white px-2.5 py-2">
            <div className="mb-0.5 flex items-center gap-1.5">
              {wfok ? <Check className="size-3.5 shrink-0 text-emerald-500" /> : <XCircle className="size-3.5 shrink-0 text-rose-500" />}
              <span className="font-semibold text-slate-700">工作流 {wi + 1}</span>
              {asStr(o.workflow_outcome) && <span className="min-w-0 text-slate-400"><MathText text={asStr(o.workflow_outcome)} className="inline" /></span>}
            </div>
            {asArr(o.steps).map((st, si) => (
              <ExecStep key={si} step={asObj(st)} />
            ))}
            {issues.length > 0 && (
              <div className="mt-1 rounded bg-rose-50 px-2 py-1 text-[10px] text-rose-600">
                {issues.slice(0, 5).map((x, i) => (
                  <div key={i}><MathText text={itemText(x, "issue", "message")} className="inline" /></div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ReflectionView({ d }: { d: Record<string, unknown> }) {
  const decision = asStr(d.decision);
  const problems = asArr(d.identified_problems);
  const actions = asArr(d.recommended_actions);
  const revisions = [...asArr(d.workflow_revision_instructions), ...asArr(d.hypothesis_revision_instructions)];
  const dtone =
    decision === "accept"
      ? "bg-emerald-50 text-emerald-700 ring-emerald-500/20"
      : decision === "stop"
        ? "bg-slate-100 text-slate-600 ring-slate-400/20"
        : "bg-amber-50 text-amber-700 ring-amber-500/20";
  return (
    <div className="space-y-2 text-[11px]">
      <div className="flex items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ring-inset ${dtone}`}>{DECISION_LABEL[decision] || decision || "—"}</span>
        <ConfidenceBar value={d.confidence} />
      </div>
      {asStr(d.reasoning) && <div className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-slate-700"><LongText text={asStr(d.reasoning)} /></div>}
      {problems.length > 0 && (
        <div>
          <Sub>发现的问题</Sub>
          <ul className="space-y-0.5">
            {problems.map((p, i) => (
              <li key={i} className="flex gap-1.5 text-slate-600">
                <AlertTriangle className="mt-0.5 size-3 shrink-0 text-amber-500" />
                <MathText text={itemText(p, "problem", "issue")} className="inline" />
              </li>
            ))}
          </ul>
        </div>
      )}
      {actions.length > 0 && (
        <div>
          <Sub>建议动作</Sub>
          <ol className="list-decimal space-y-0.5 pl-4 text-slate-600">
            {actions.map((a, i) => (
              <li key={i}><MathText text={itemText(a, "action", "recommendation")} className="inline" /></li>
            ))}
          </ol>
        </div>
      )}
      {revisions.length > 0 && (
        <div>
          <Sub>修订指令</Sub>
          <ul className="list-disc space-y-0.5 pl-4 text-slate-500">
            {revisions.map((r, i) => (
              <li key={i}><MathText text={itemText(r, "instruction", "text")} className="inline" /></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function stepKind(step: string): "retrieval" | "hypothesis" | "planner" | "execution" | "reflection" | "unknown" {
  if (/reflection_phase_round/.test(step)) return "reflection";
  if (step === "retrieval_phase") return "retrieval";
  if (step === "hypothesis_phase") return "hypothesis";
  if (step === "planner_phase") return "planner";
  if (step === "execution_phase") return "execution";
  return "unknown";
}

/** 调度：按步骤类型选定制渲染器；未知形态或显式切换时回退通用 JsonView（含「查看原始 JSON」）。 */
function StepArtifactView({ stepName, data }: { stepName: string; data: unknown }) {
  const [raw, setRaw] = useState(false);
  const kind = stepKind(stepName);
  const d = asObj(data);
  const tailored = kind !== "unknown" && Object.keys(d).length > 0;
  return (
    <div>
      {tailored && !raw ? (
        kind === "retrieval" ? (
          <RetrievalView d={d} />
        ) : kind === "hypothesis" ? (
          <HypothesisView d={d} />
        ) : kind === "planner" ? (
          <PlannerView d={d} />
        ) : kind === "execution" ? (
          <ExecutionView d={d} />
        ) : (
          <ReflectionView d={d} />
        )
      ) : (
        <JsonView value={data} />
      )}
      {tailored && (
        <button type="button" onClick={() => setRaw((v) => !v)} className="mt-1.5 text-[9px] text-slate-300 transition hover:text-slate-500">
          {raw ? "← 返回可视化" : "查看原始 JSON"}
        </button>
      )}
    </div>
  );
}

export function artifactName(f: string | ArtifactFile): string {
  return typeof f === "string" ? f : f.name;
}

export function artifactSize(f: string | ArtifactFile): number | undefined {
  return typeof f === "string" ? undefined : f.size;
}

export function formatBytes(n?: number): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function canPreviewText(name: string): boolean {
  return /\.(json|txt|log|out|gjf|sdf|xyz|csv|tsv|md|py|sh|yaml|yml)$/i.test(name);
}

export function ArtifactPreview({ runId, name, stepName }: { runId: string; name: string; stepName: string }) {
  const [state, setState] = useState<{ loading: boolean; data?: unknown; err?: string }>({ loading: true });
  useEffect(() => {
    setState({ loading: true });
    if (!canPreviewText(name)) {
      setState({ loading: false, err: "该文件类型不支持内联预览" });
      return;
    }
    let alive = true;
    fetch(archeApi.artifactUrl(runId, name))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((text) => {
        if (!alive) return;
        if (/\.json$/i.test(name)) {
          try {
            setState({ loading: false, data: JSON.parse(text) });
          } catch {
            setState({ loading: false, data: text });
          }
          return;
        }
        setState({ loading: false, data: text });
      })
      .catch((e) => alive && setState({ loading: false, err: (e as Error).message }));
    return () => {
      alive = false;
    };
  }, [runId, name]);
  if (state.loading)
    return (
      <div className="flex items-center gap-1.5 px-2 py-1.5 text-[10px] text-slate-400">
        <Loader2 className="size-3 animate-spin" /> 加载预览…
      </div>
    );
  if (state.err)
    return (
      <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-400">
        {state.err}
      </div>
    );
  if (typeof state.data === "string") {
    return (
      <pre className="console-scroll max-h-[25rem] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-[10px] leading-relaxed text-slate-200">
        {state.data || "（空文件）"}
      </pre>
    );
  }
  return (
    <div className="console-scroll max-h-[25rem] overflow-auto rounded-lg border border-slate-200 bg-white px-3 py-2">
      <StepArtifactView stepName={stepName} data={state.data} />
    </div>
  );
}

function ProcessCard({
  step,
  index,
  selected,
  onSelect,
  artifactNames,
}: {
  step: TimelineStep;
  index: number;
  selected: boolean;
  onSelect: () => void;
  artifactNames: Set<string>;
}) {
  const { label, Icon } = stepInfo(step.step);
  const tone = STATUS_TONE[step.status] || STATUS_TONE.started;
  const time = step.timestamp ? step.timestamp.slice(11, 19) : "";
  const entries = Object.entries(step.data || {}).filter(
    ([k, v]) => !HIDDEN_KEYS.has(k) && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0),
  );
  const artName = stepArtifactName(step.step);
  const hasFile = !!artName && artifactNames.has(artName);

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`group block w-full rounded-lg border px-3 py-3 text-left transition ${
        selected
          ? "border-[#14532d] bg-white shadow-[0_18px_38px_rgba(20,83,45,0.12)] ring-1 ring-[#14532d]/10"
          : "border-slate-200 bg-white/85 shadow-sm hover:border-slate-300 hover:bg-white"
      }`}
    >
      <div className="flex items-start gap-3">
        <div
          className={`flex size-8 shrink-0 items-center justify-center rounded-md ${
            selected ? "bg-[#0b1f17] text-white" : "bg-slate-100 text-slate-500 group-hover:bg-slate-200"
          }`}
        >
          <Icon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="font-mono text-[10px] font-semibold text-slate-300">{String(index + 1).padStart(2, "0")}</span>
            <span className="truncate text-sm font-semibold text-slate-800">{label}</span>
            <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset ${tone.badge}`}>
              {tone.text}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-400">
            <span className="inline-flex items-center gap-1">
              <span className={`size-1.5 rounded-full ${tone.dot}`} />
              {time || "—"}
            </span>
            {hasFile && (
              <span className="inline-flex items-center gap-1 text-[#14532d]">
                <FileJson className="size-3" /> JSON
              </span>
            )}
            {entries.length > 0 && <span>{entries.length} 项摘要</span>}
          </div>
          {entries.length > 0 && (
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {entries.slice(0, 4).map(([k, v]) => {
                const danger = (k === "fallback_triggered" && v === true) || k === "error";
                const accent = k === "decision";
                return (
                  <div key={k} className="min-w-0 rounded-md bg-slate-50 px-2 py-1">
                    <div className="truncate text-[10px] text-slate-400" title={KEY_HINT[k]}>
                      {prettyKey(k)}
                    </div>
                    <div className={`truncate text-[11px] ${danger ? "font-medium text-rose-600" : accent ? "font-semibold text-[#14532d]" : "text-slate-600"}`}>
                      {fmtScalar(k, v)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}

function ProcessInspector({
  step,
  runId,
  artifacts,
  artifactNames,
}: {
  step: TimelineStep;
  runId?: string;
  artifacts: Array<string | ArtifactFile>;
  artifactNames: Set<string>;
}) {
  const { label, Icon } = stepInfo(step.step);
  const tone = STATUS_TONE[step.status] || STATUS_TONE.started;
  const primaryName = stepArtifactName(step.step);
  const primaryAvailable = !!primaryName && artifactNames.has(primaryName);
  const [selectedFile, setSelectedFile] = useState<string | null>(primaryAvailable ? primaryName : null);

  useEffect(() => {
    setSelectedFile(primaryAvailable ? primaryName : null);
  }, [primaryAvailable, primaryName, step.step]);

  const files = [
    ...artifacts.filter((f) => primaryName && artifactName(f) === primaryName),
    ...artifacts.filter((f) => !primaryName || artifactName(f) !== primaryName),
  ];
  const summaryEntries = Object.entries(step.data || {}).filter(
    ([k, v]) => !HIDDEN_KEYS.has(k) && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0),
  );

  return (
    <aside className="min-w-0 rounded-lg border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.06)] lg:sticky lg:top-3">
      <div className="border-b border-slate-200 bg-[#fbfcfb] px-4 py-3">
        <div className="flex items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[#0b1f17] text-white">
            <Icon className="size-4" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-slate-900">{label}</span>
              <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset ${tone.badge}`}>
                {tone.text}
              </span>
            </div>
            <div className="mt-1 font-mono text-[10px] text-slate-400">{step.step}</div>
          </div>
          <PanelRightOpen className="mt-0.5 size-4 shrink-0 text-slate-300" />
        </div>
      </div>

      <div className="space-y-4 px-4 py-4">
        {summaryEntries.length > 0 && (
          <div>
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400">阶段摘要</div>
            <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              {summaryEntries.slice(0, 8).map(([k, v]) => {
                const danger = (k === "fallback_triggered" && v === true) || k === "error";
                const accent = k === "decision";
                return (
                  <div key={k} className="rounded-md border border-slate-100 bg-slate-50 px-2.5 py-1.5">
                    <div className="text-[10px] text-slate-400" title={KEY_HINT[k]}>
                      {prettyKey(k)}
                    </div>
                    <div className={`mt-0.5 break-words text-[11px] ${danger ? "font-medium text-rose-600" : accent ? "font-semibold text-[#14532d]" : "text-slate-700"}`}>
                      {fmtScalar(k, v)}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">流程文件</div>
            {selectedFile && runId && (
              <a
                href={archeApi.artifactUrl(runId, selectedFile)}
                download={selectedFile}
                className="inline-flex items-center gap-1 rounded-md bg-slate-50 px-2 py-1 text-[10px] font-medium text-[#14532d] ring-1 ring-inset ring-slate-200 transition hover:bg-[#f3f8f5]"
              >
                <Download className="size-3" /> 下载
              </a>
            )}
          </div>
          {files.length > 0 ? (
            <div className="console-scroll flex max-h-28 flex-col gap-1 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-1">
              {files.slice(0, 24).map((f) => {
                const name = artifactName(f);
                const size = artifactSize(f);
                const selected = selectedFile === name;
                const stageFile = primaryName === name;
                return (
                  <button
                    type="button"
                    key={name}
                    onClick={() => setSelectedFile(name)}
                    className={`flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left transition ${
                      selected ? "bg-white text-[#14532d] shadow-sm ring-1 ring-inset ring-[#b7d4c0]" : "text-slate-600 hover:bg-white"
                    }`}
                  >
                    {name.endsWith(".json") ? <FileJson className="size-3.5 shrink-0" /> : <FileText className="size-3.5 shrink-0" />}
                    <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{name}</span>
                    {stageFile && <span className="rounded bg-[#eef7f1] px-1.5 py-0.5 text-[9px] font-medium text-[#14532d]">阶段</span>}
                    {size !== undefined && <span className="shrink-0 text-[9px] text-slate-400">{formatBytes(size)}</span>}
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-center text-xs text-slate-400">
              暂无可下载产物
            </div>
          )}
        </div>

        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            <Eye className="size-3" /> 预览
          </div>
          {selectedFile && runId ? (
            <ArtifactPreview runId={runId} name={selectedFile} stepName={step.step} />
          ) : step.data && Object.keys(step.data).length > 0 ? (
            <div className="console-scroll max-h-[25rem] overflow-auto rounded-lg border border-slate-200 bg-white px-3 py-2">
              <StepArtifactView stepName={step.step} data={step.data} />
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-400">
              该阶段尚未产生预览内容
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

/** multiagent_log.json 是「每阶段 started + completed 两条事件」的扁平流。按 step 折叠成
 *  每阶段一个节点：状态取终态（completed/failed/waiting 优先于 started），data 合并（后到覆盖）。
 *  于是已完成阶段只显示一次「完成」，仅真正没有 completed 的当前阶段才显示「进行中」——不再
 *  出现「已完成的 run 却把每步同时显示成 进行中 + 完成」的重影。 */
const TERMINAL_STATUS = new Set(["completed", "failed", "waiting_for_gaussian_jobs"]);
export function collapseTimeline(events: TimelineStep[]): TimelineStep[] {
  const order: string[] = [];
  const byStep = new Map<string, TimelineStep>();
  for (const ev of events) {
    const prev = byStep.get(ev.step);
    if (!prev) {
      order.push(ev.step);
      byStep.set(ev.step, { ...ev, data: { ...(ev.data || {}) } });
      continue;
    }
    const merged: TimelineStep = { ...prev, data: { ...(prev.data || {}), ...(ev.data || {}) } };
    // 终态优先：记到终态后不被后来的 started 盖回；后到的终态（如 waiting→completed）可覆盖。
    if (TERMINAL_STATUS.has(ev.status) || !TERMINAL_STATUS.has(prev.status)) {
      merged.status = ev.status;
      merged.timestamp = ev.timestamp ?? prev.timestamp;
    }
    byStep.set(ev.step, merged);
  }
  return order.map((k) => byStep.get(k) as TimelineStep);
}

/** 研究过程时间线：把 controller 的 multiagent_log.json 渲染成可读、可下钻的科研全过程。
 *  实时完成态与历史回看共用（数据均来自 RunResult.timeline）；每步可下钻到对应分阶段 JSON 全文。 */
export function ResearchTimeline({
  timeline,
  runId,
  artifacts,
}: {
  timeline?: TimelineStep[];
  runId?: string;
  artifacts?: Array<string | ArtifactFile>;
}) {
  const [open, setOpen] = useState(true);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const steps = collapseTimeline(timeline || []);
  useEffect(() => {
    setSelectedIndex((i) => Math.min(i, Math.max(steps.length - 1, 0)));
  }, [steps.length]);
  if (steps.length === 0) return null;

  const artifactNames = new Set((artifacts || []).map((a) => (typeof a === "string" ? a : a.name)));
  const failed = steps.filter((s) => s.status === "failed").length;
  const rounds = new Set(steps.map((s) => s.step.match(/round_(\d+)/)?.[1]).filter(Boolean)).size;
  const selectedStep = steps[selectedIndex] || steps[0];

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-slate-400 transition hover:text-slate-600"
      >
        <ChevronDown className={`size-3.5 transition-transform ${open ? "rotate-0" : "-rotate-90"}`} />
        研究过程时间线（{steps.length} 步{rounds ? ` · ${rounds} 轮闭环` : ""}）
        {failed > 0 && (
          <span className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 text-[10px] font-medium text-rose-600 ring-1 ring-inset ring-rose-500/20">
            <XCircle className="size-3" /> {failed} 步失败
          </span>
        )}
      </button>
      {open && (
        <div className="mt-2 grid gap-3 lg:grid-cols-[minmax(0,0.86fr)_minmax(360px,0.72fr)]">
          <div className="console-scroll max-h-[38rem] space-y-2 overflow-auto pr-1">
            {steps.map((step, i) => (
              <ProcessCard
                key={`${step.step}-${step.timestamp ?? i}`}
                step={step}
                index={i}
                selected={i === selectedIndex}
                onSelect={() => setSelectedIndex(i)}
                artifactNames={artifactNames}
              />
            ))}
            <div className="flex items-center gap-1.5 px-1 py-1 text-[10px] text-slate-300">
              {failed > 0 ? <AlertTriangle className="size-3" /> : <Check className="size-3" />}
              <span>{failed > 0 ? "过程含失败步骤，可在右侧预览排查" : "全过程完成"}</span>
              <Hourglass className="ml-auto size-3 opacity-0" />
            </div>
          </div>
          {selectedStep && <ProcessInspector step={selectedStep} runId={runId} artifacts={artifacts || []} artifactNames={artifactNames} />}
        </div>
      )}
    </div>
  );
}
