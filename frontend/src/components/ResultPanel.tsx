import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleSlash,
  Copy,
  Download,
  Info,
  Lightbulb,
  ListChecks,
  Loader2,
  TerminalSquare,
  Workflow,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { archeApi } from "../api";
import { MathText } from "../lib/katex";
import { diagnose, parseSummary } from "../lib/parse";
import type { ArcheRunResult, RunResult, RunSummary } from "../types";
import { ResearchTimeline } from "./ResearchTimeline";
import { RunHealthBanner } from "./RunHealthBanner";

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

type DisplayPair = { label: string; value: string };

const SETTING_LABELS: Record<string, string> = {
  method: "方法",
  basis_set: "基组",
  functional: "泛函",
  level_of_theory: "理论水平",
  software: "软件",
  route: "Route",
  charge: "电荷",
  multiplicity: "自旋多重度",
  spin_multiplicity: "自旋多重度",
  solvation_model: "溶剂化模型",
  solvent: "溶剂",
  temperature: "温度",
  suspected_job_types: "任务类型",
  needs_ts: "需要过渡态",
  needs_irc: "需要 IRC",
  needs_excited_state: "需要激发态",
};

const NOISY_CONTEXT_KEYS = new Set([
  "candidate_elements",
  "species_roles",
  "evidence_gaps",
  "reaction_type",
  "mechanistic_goal",
]);

function dedupe(items: string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = item.trim();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function questionMentions(question: string | undefined, words: string[]): boolean {
  const q = String(question ?? "").toLowerCase();
  return words.some((w) => q.includes(w));
}

function normalizeSettingValue(key: string, value: unknown, question?: string): string {
  if (value == null || value === "") return "";
  if (typeof value === "string") {
    const text = value.trim();
    if (!text || /^(unknown|none|null|n\/a|not specified)$/i.test(text)) return "";
    if (key === "solvent" && /^(water|h2o)$/i.test(text) && !questionMentions(question, ["溶剂", "水溶液", "aqueous", "solution", "solvent"])) return "";
    if (key === "temperature" && !questionMentions(question, ["温度", "temperature", " kelvin", " k ", "℃", "°c"])) return "";
    return text;
  }
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "boolean") {
    if (!value) return "";
    if (key === "needs_ts" && !questionMentions(question, ["过渡态", "transition state", " ts ", "势垒", "barrier"])) return "";
    if (key === "needs_irc" && !questionMentions(question, ["irc", "反应路径", "pathway", "intrinsic reaction coordinate"])) return "";
    if (key === "needs_excited_state" && !questionMentions(question, ["激发态", "excited", "td-dft", "tddft"])) return "";
    return "是";
  }
  if (Array.isArray(value)) {
    let items = value.map((v) => valText(v)).filter(Boolean);
    if (key === "suspected_job_types") {
      items = items.filter((item) => {
        const low = item.toLowerCase();
        if (low === "ts") return questionMentions(question, ["过渡态", "transition state", " ts ", "势垒", "barrier"]);
        if (low === "irc") return questionMentions(question, ["irc", "反应路径", "pathway", "intrinsic reaction coordinate"]);
        return true;
      });
    }
    return dedupe(items).join("、");
  }
  return "";
}

function filterContextEntries(ctx: Record<string, unknown>, question?: string): DisplayPair[] {
  return Object.entries(ctx)
    .filter(([key]) => !NOISY_CONTEXT_KEYS.has(key))
    .map(([key, value]) => ({ label: SETTING_LABELS[key] ?? key, value: normalizeSettingValue(key, value, question) }))
    .filter((entry) => entry.value);
}

function findComputedResult(findings: unknown[]): Record<string, unknown> | null {
  for (const item of findings) {
    if (item && typeof item === "object" && (item as Record<string, unknown>).type === "computed_results") {
      return item as Record<string, unknown>;
    }
  }
  return null;
}

function formatComputedResults(computed: Record<string, unknown> | null): DisplayPair[] {
  if (!computed) return [];
  const out: DisplayPair[] = [];
  if (computed.homo_lumo_gap_ev != null) out.push({ label: "HOMO-LUMO 能隙", value: `${computed.homo_lumo_gap_ev} eV` });
  if (computed.scf_energy_hartree != null) out.push({ label: "SCF 能量", value: `${computed.scf_energy_hartree} Hartree` });
  if (computed.homo_hartree != null) out.push({ label: "HOMO", value: `${computed.homo_hartree} Ha` });
  if (computed.lumo_hartree != null) out.push({ label: "LUMO", value: `${computed.lumo_hartree} Ha` });
  if (Array.isArray(computed.ir_peaks_cm1) && computed.ir_peaks_cm1.length > 0) {
    out.push({ label: "主要红外峰", value: `${computed.ir_peaks_cm1.slice(0, 4).map((v) => Number(v).toFixed(0)).join("、")} cm⁻¹` });
  }
  if (computed.strongest_absorption_nm != null) {
    const ev = computed.strongest_absorption_ev != null ? ` / ${computed.strongest_absorption_ev} eV` : "";
    out.push({ label: "UV-Vis 吸收", value: `${computed.strongest_absorption_nm} nm${ev}` });
  }
  return out;
}

function normalizeConclusionSummary(raw: unknown, results: DisplayPair[], question?: string): string {
  const text = typeof raw === "string" ? raw.trim() : "";
  const hasOldTemplate = /Preliminary results for|require further validation|真实 Gaussian 计算结果:/i.test(text);
  if (!hasOldTemplate) return text;
  if (!results.length) {
    return text
      .replace(/真实 Gaussian 计算结果:\s*/i, "本次计算得到：")
      .replace(/\s*Preliminary results for ['"].*?require further validation\./i, " 仍需复核后再用于正式结论。")
      .trim();
  }
  const subject = question ? `针对“${question}”，` : "";
  const facts = results.map((item) => `${item.label}为 ${item.value}`).join("；");
  return `${subject}本次计算得到：${facts}。这些结果可作为本轮计算证据；仍需结合失败步骤、几何参数和文献数据复核后再用于正式结论。`;
}

function humanizeIssue(text: string): string {
  if (/steps为空|steps\s*为空/i.test(text)) return "部分规划协议没有生成可执行步骤。";
  if (/unsupported_subprocess_cli/i.test(text)) return "部分工具缺少可用 CLI 映射，相关步骤未完成。";
  if (/workflow required revisions|执行期间发生过修订/i.test(text)) return "工作流执行期间发生过修订，结论应结合修订记录复核。";
  return text;
}

function humanizeNextStep(text: string): string {
  if (/debug failed computational steps/i.test(text)) return "修复失败的计算或绘图步骤。";
  if (/compare computational results with literature findings/i.test(text)) return "将计算结果与文献数据逐项对照。";
  if (/run additional validation calculations/i.test(text)) return "补充验证计算（更大基组或更高层级方法）。";
  if (/expand literature search/i.test(text)) return "扩展文献检索以补强对照证据。";
  return text.endsWith("。") || text.endsWith(".") ? text : `${text}。`;
}

function computeConclusionView(sci: ArcheRunResult) {
  const c = sci.final_conclusion ?? {};
  const findings = Array.isArray(c.key_findings) ? c.key_findings : [];
  const computed = findComputedResult(findings);
  const ctx = (sci.shared_state?.chemistry_context ?? {}) as Record<string, unknown>;
  const results = formatComputedResults(computed);
  const question = c.scientific_question || sci.scientific_question;
  return {
    summary: normalizeConclusionSummary(c.conclusion_summary, results, question),
    confidence: typeof c.confidence === "number" ? c.confidence : undefined,
    conclusionType: c.conclusion_type ? String(c.conclusion_type) : "",
    results,
    limitations: dedupe((Array.isArray(c.unresolved_issues) ? c.unresolved_issues : []).map(valText).map(humanizeIssue).filter(Boolean)),
    nextSteps: dedupe((Array.isArray(c.recommended_next_steps) ? c.recommended_next_steps : []).map(valText).map(humanizeNextStep).filter(Boolean)),
    settings: filterContextEntries(ctx, question),
  };
}

/** P1-B：把结论 + 设置导出为 Markdown，便于贴进组会/SI/论文。 */
function toMarkdown(result: RunResult, summary: RunSummary, sci: ArcheRunResult | null): string {
  const lines: string[] = ["# ARCHE 计算化学工作流结果"];
  const q = summary.question || sci?.scientific_question || "";
  if (q) lines.push(`\n**研究问题**：${q}`);
  if (sci) {
    const view = computeConclusionView(sci);
    if (view.summary) lines.push(`\n## 结论\n${view.summary}`);
    if (view.results.length) lines.push(`\n## 计算结果\n${view.results.map((r) => `- ${r.label}: ${r.value}`).join("\n")}`);
    if (view.limitations.length) lines.push(`\n## 限制\n${view.limitations.map((it) => `- ${it}`).join("\n")}`);
    if (view.nextSteps.length) lines.push(`\n## 下一步\n${view.nextSteps.map((it) => `- ${it}`).join("\n")}`);
    if (view.settings.length) {
      lines.push(`\n## 计算设置\n${view.settings.map((entry) => `- ${entry.label}: ${entry.value}`).join("\n")}`);
    }
  }
  const ts = result.createdAt ? new Date(result.createdAt).toISOString() : "";
  lines.push(`\n---\n*ARCHE · run ${result.id ?? ""} · ${ts} · exit ${result.exitCode}*`);
  return lines.join("\n");
}

// 结论类型枚举 → 人类可读中文(不再原样吐 "provisional" 这种内部英文枚举)
const CONCLUSION_TYPE_LABEL: Record<string, string> = {
  supported: "有计算证据支持",
  provisional: "暂定结论",
  failed: "未得出结论",
};

function valText(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    // 计算结果类:把真实算出的化学量拼成人类可读句子
    if (o.type === "computed_results") {
      const parts: string[] = [];
      if (o.homo_lumo_gap_ev != null) parts.push(`HOMO-LUMO gap = ${o.homo_lumo_gap_ev} eV`);
      if (o.scf_energy_hartree != null) parts.push(`SCF energy = ${o.scf_energy_hartree} Hartree`);
      if (o.homo_hartree != null) parts.push(`HOMO = ${o.homo_hartree} Ha`);
      if (o.lumo_hartree != null) parts.push(`LUMO = ${o.lumo_hartree} Ha`);
      const head = String(o.summary ?? "真实计算结果");
      return parts.length ? `${head}：${parts.join("，")}` : head;
    }
    // 策略类:展示策略名(+ 理由),而不是吐整个 JSON
    if (o.strategy_name != null || o.reasoning != null) {
      const name = o.strategy_name != null ? String(o.strategy_name) : "";
      const reason = o.reasoning != null ? String(o.reasoning) : "";
      return name && reason ? `${name} —— ${reason}` : name || reason;
    }
    // 通用:挑常见的人类可读字段
    const text = o.summary ?? o.name ?? o.description ?? o.text ?? o.conclusion ?? o.value ?? o.label ?? o.finding ?? o.content;
    if (text != null) return String(text);
    // 兜底:绝不吐裸 JSON —— 平铺成 "键: 值" 可读串
    const entries = Object.entries(o).filter(([k, val]) => k !== "type" && val != null && typeof val !== "object");
    return entries.map(([k, val]) => `${k}: ${val}`).join("，");
  }
  return String(v);
}

function ResultMetric({ item }: { item: DisplayPair }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2.5">
      <div className="text-[11px] font-medium text-slate-500">{item.label}</div>
      <div className="mt-1 break-words font-mono text-sm font-semibold leading-snug text-slate-900">{item.value}</div>
    </div>
  );
}

function TextList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      <ul className="space-y-1.5 text-sm text-slate-700">
        {items.map((it) => (
          <li key={it} className="flex gap-2 leading-relaxed">
            <span className="mt-2 size-1 shrink-0 rounded-full bg-slate-400" />
            <MathText text={it} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function SettingsGrid({ items }: { items: DisplayPair[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        <ListChecks className="size-3" /> 计算设置
      </div>
      <div className="grid grid-cols-1 gap-x-5 gap-y-1 text-xs sm:grid-cols-2">
        {items.map((item) => (
          <div key={`${item.label}:${item.value}`} className="flex justify-between gap-3 border-b border-slate-200/80 py-1">
            <span className="text-slate-500">{item.label}</span>
            <span className="min-w-0 break-words text-right font-mono text-slate-800">{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 「科学结论」区：渲染 controller 真实产出的最终结论 + 计算设置。 */
function ConclusionSection({ sci }: { sci: ArcheRunResult }) {
  const view = computeConclusionView(sci);
  const typeLabel = view.conclusionType ? CONCLUSION_TYPE_LABEL[view.conclusionType] : "";
  if (!view.summary && view.results.length === 0 && view.limitations.length === 0 && view.nextSteps.length === 0 && view.settings.length === 0) return null;

  return (
    <div className="-mx-5 border-y border-slate-200 bg-slate-50/70 px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <Lightbulb className="size-4 text-[#14532d]" />
        <span className="text-xs font-semibold text-[#14532d]">科学结论</span>
        {typeLabel && (
          <span className="rounded bg-white px-1.5 py-0.5 text-[10px] text-[#14532d] ring-1 ring-inset ring-slate-200">
            {typeLabel}
          </span>
        )}
        {view.confidence !== undefined && (
          <span className="ml-auto text-[11px] text-slate-500">置信度 {Math.round(view.confidence * 100)}%</span>
        )}
      </div>

      {view.summary ? (
        <MathText text={view.summary} className="block max-w-[78ch] text-[15px] leading-relaxed text-slate-900" />
      ) : (
        <p className="text-sm text-slate-400">本次未生成明确结论（多为模型不可达 / 降级运行）。</p>
      )}

      {view.results.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">计算结果</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {view.results.map((item) => <ResultMetric key={item.label} item={item} />)}
          </div>
        </div>
      )}

      {(view.limitations.length > 0 || view.nextSteps.length > 0) && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <TextList title="限制" items={view.limitations} />
          <TextList title="下一步" items={view.nextSteps} />
        </div>
      )}

      {view.settings.length > 0 && (
        <div className="mt-4 border-t border-slate-200 pt-3">
          <SettingsGrid items={view.settings} />
        </div>
      )}
    </div>
  );
}

const TONE = {
  ok: { bar: "bg-emerald-50", text: "text-emerald-700", Icon: CheckCircle2 },
  warn: { bar: "bg-amber-50", text: "text-amber-700", Icon: AlertTriangle },
  error: { bar: "bg-rose-50", text: "text-rose-700", Icon: XCircle },
  running: { bar: "bg-amber-50", text: "text-amber-700", Icon: Loader2 },
  cancelled: { bar: "bg-slate-50", text: "text-slate-600", Icon: CircleSlash },
} as const;

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-center">
      <div className="font-mono text-xl font-semibold text-slate-800">{value}</div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
    </div>
  );
}

function fmtTime(ms?: number): string {
  if (!ms) return "";
  try {
    return new Date(ms).toLocaleString();
  } catch {
    return "";
  }
}

const DECISION_TONE: Record<string, string> = {
  accept: "bg-emerald-50 text-emerald-700",
  revise_workflow: "bg-amber-100 text-amber-700",
  revise: "bg-amber-100 text-amber-700",
  stop: "bg-slate-100 text-slate-600",
};

/** P1-A：分阶段证据/推理可见 —— 检索证据 + 反馈闭环逐轮决策（折叠）。 */
function ProcessDetails({ sci }: { sci: ArcheRunResult }) {
  const [open, setOpen] = useState(false);
  const retrieval = (sci.retrieval_phase ?? {}) as Record<string, unknown>;
  const keywords = (Array.isArray(retrieval.keywords) ? retrieval.keywords : []).map(valText).filter(Boolean);
  const excerptsRaw = retrieval.relevant_excerpts ?? retrieval.excerpts ?? retrieval.evidence;
  const excerpts = (Array.isArray(excerptsRaw) ? excerptsRaw : []) as Record<string, unknown>[];
  const rounds = (Array.isArray(sci.reflection_rounds) ? sci.reflection_rounds : []) as Record<string, unknown>[];
  if (keywords.length === 0 && excerpts.length === 0 && rounds.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
      >
        <span className="inline-flex items-center gap-1.5">
          <Workflow className="size-4 text-slate-400" /> 智能体过程明细
        </span>
        <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-4 border-t border-slate-100 px-4 py-3">
          {(keywords.length > 0 || excerpts.length > 0) && (
            <div>
              <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400">检索证据</div>
              {keywords.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1">
                  {keywords.map((k) => (
                    <span key={k} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                      {k}
                    </span>
                  ))}
                </div>
              )}
              {excerpts.slice(0, 8).map((ex, i) => {
                const text = valText(ex.text ?? ex.excerpt ?? ex.content ?? ex.summary ?? ex);
                const source = valText(ex.source ?? ex.ref ?? ex.title ?? ex.doi);
                const score = typeof ex.score === "number" ? ex.score : typeof ex.similarity === "number" ? ex.similarity : undefined;
                if (!text) return null;
                return (
                  <div key={`${source}-${i}`} className="mb-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                    <div className="text-xs leading-relaxed text-slate-700">{text}</div>
                    {(source || score !== undefined) && (
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-400">
                        {source && <span className="truncate">{source}</span>}
                        {score !== undefined && <span className="font-mono">相似度 {score.toFixed(2)}</span>}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {rounds.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400">反馈闭环逐轮</div>
              <div className="space-y-2">
                {rounds.map((r, i) => {
                  const decision = String(r.decision ?? "");
                  const problems = (Array.isArray(r.identified_problems) ? r.identified_problems : []).map(valText).filter(Boolean);
                  const actions = (Array.isArray(r.recommended_actions) ? r.recommended_actions : []).map(valText).filter(Boolean);
                  return (
                    <div key={`round-${valText(r.round) || i}`} className="rounded-lg border border-slate-100 px-3 py-2">
                      <div className="mb-1 flex items-center gap-2">
                        <span className="text-xs font-semibold text-slate-600">轮次 {valText(r.round) || i + 1}</span>
                        {decision && (
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${DECISION_TONE[decision] ?? "bg-slate-100 text-slate-600"}`}>
                            {decision}
                          </span>
                        )}
                      </div>
                      {problems.length > 0 && (
                        <div className="text-xs text-slate-600">
                          <span className="text-slate-400">发现的问题：</span>
                          {problems.join("；")}
                        </div>
                      )}
                      {actions.length > 0 && (
                        <div className="text-xs text-slate-600">
                          <span className="text-slate-400">建议措施：</span>
                          {actions.join("；")}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ResultPanel({
  result,
  error,
  showTimeline = true,
  showArtifacts = true,
  showRunningBanner = true,
  showStatusHeader = true,
  showHealthBanner = true,
}: {
  result: RunResult | null;
  error: string | null;
  showTimeline?: boolean;
  showArtifacts?: boolean;
  showRunningBanner?: boolean;
  showStatusHeader?: boolean;
  showHealthBanner?: boolean;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const [artifactsOpen, setArtifactsOpen] = useState(false);

  if (error) {
    return (
      <section className="w-full overflow-hidden rounded-lg border border-rose-200 bg-white shadow-sm">
        <div className="flex items-center gap-2 bg-rose-50 px-5 py-3 text-rose-700">
          <XCircle className="size-5" />
          <span className="text-sm font-semibold">请求失败</span>
        </div>
        <p className="px-5 py-4 text-sm text-slate-600">{error}</p>
      </section>
    );
  }
  if (!result) return null;

  const summary = parseSummary(result.stdout || "");
  const sci = result.result ?? null;
  const diag = diagnose(`${result.stdout || ""}\n${result.stderr || ""}`, result.exitCode, result.status);
  const tone = TONE[diag.tone];
  const isRunning = diag.tone === "running";
  const stats: Array<{ label: string; value: string }> = [];
  if (summary.stages !== undefined) stats.push({ label: "阶段数", value: String(summary.stages) });
  if (summary.totalSteps !== undefined) stats.push({ label: "总步骤", value: String(summary.totalSteps) });
  if (summary.successRate) stats.push({ label: "成功率", value: summary.successRate });
  if (summary.durationSeconds !== undefined) stats.push({ label: "耗时", value: `${summary.durationSeconds.toFixed(1)}s` });
  const rawLog = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();

  return (
    <section className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.07)]">
      {/* 状态横幅：作为会话流报告的第一眼可信度提示。 */}
      {showStatusHeader && (
        <div className={`flex shrink-0 items-center justify-between px-5 py-3.5 ${tone.bar}`}>
          <div className={`flex items-center gap-2 ${tone.text}`}>
            <tone.Icon className={`size-5 ${isRunning ? "animate-spin" : ""}`} />
            <span className="text-sm font-semibold">{diag.title}</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-slate-500">
            {fmtTime(result.createdAt) && <span className="font-mono">{fmtTime(result.createdAt)}</span>}
            <button
              type="button"
              title="复制结论（Markdown）"
              onClick={() => {
                navigator.clipboard?.writeText(toMarkdown(result, summary, sci)).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                });
              }}
              className="inline-flex items-center gap-1 rounded bg-white/70 px-1.5 py-0.5 ring-1 ring-inset ring-slate-200 transition hover:text-slate-700"
            >
              {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
              {copied ? "已复制" : "复制"}
            </button>
          </div>
        </div>
      )}

      <div className={`space-y-5 px-5 ${showStatusHeader ? "py-4" : "pb-4"}`}>
        {/* 运行中回看：结论/时间线尚未落盘，明确告知仍在进行、本页会自动收敛到最终结果。 */}
        {isRunning && showRunningBanner && (
          <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/60 px-4 py-3 text-sm text-amber-700">
            <Loader2 className="size-4 shrink-0 animate-spin" />
            <span>运行仍在进行中，完成后本页会自动刷新为最终结果。</span>
          </div>
        )}

        {/* 运行健康度:第一眼判断这份结论可不可信（含降级/模拟/失败则醒目提示） */}
        {showHealthBanner && <RunHealthBanner sci={sci} />}

        {/* 研究问题不在此重复展示 —— 详情页顶部已置顶问题卡片。 */}

        {sci && <ConclusionSection sci={sci} />}
        {sci && <ProcessDetails sci={sci} />}

        {/* 研究过程时间线：controller multiagent_log.json 的逐步事件，实时完成态与历史回看共用。
            不依赖 sci —— 即便失败/无最终结论，也能看清整个过程卡/错在哪一步。 */}
        {showTimeline && <ResearchTimeline timeline={result.timeline} runId={result.id} artifacts={result.artifacts} />}

        {/* 执行概要 */}
        {stats.length > 0 && (
          <div>
            <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-400">执行概要</div>
            <div className={`grid gap-3 ${stats.length >= 4 ? "grid-cols-2 sm:grid-cols-4" : "grid-cols-3"}`}>
              {stats.map((s) => (
                <StatCard key={s.label} label={s.label} value={s.value} />
              ))}
            </div>
          </div>
        )}

        {/* 诊断 / 提示 */}
        {(diag.hints.length > 0 || summary.errorMessage) && (
          <div className={`rounded-lg border px-4 py-3 ${diag.tone === "error" ? "border-rose-200 bg-rose-50/60" : "border-amber-200 bg-amber-50/60"}`}>
            <div className={`mb-1.5 flex items-center gap-1.5 text-xs font-semibold ${diag.tone === "error" ? "text-rose-700" : "text-amber-700"}`}>
              <Info className="size-3.5" /> 诊断
            </div>
            <ul className="space-y-1 text-sm text-slate-700">
              {diag.hints.map((h) => (
                <li key={h} className="flex gap-1.5">
                  <span className="text-slate-400">·</span>
                  <span>{h}</span>
                </li>
              ))}
              {summary.errorMessage && (
                <li className="flex gap-1.5">
                  <span className="text-slate-400">·</span>
                  <span className="font-mono text-xs text-slate-600">{summary.errorMessage}</span>
                </li>
              )}
            </ul>
          </div>
        )}

        {/* 产物文件：默认折叠，点击展开（每个文件名 + 大小 + 下载按钮，兼容旧记录纯字符串） */}
        {showArtifacts && result.artifacts && result.artifacts.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setArtifactsOpen((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400 transition hover:text-slate-600"
            >
              <ChevronDown className={`size-3.5 transition-transform ${artifactsOpen ? "rotate-0" : "-rotate-90"}`} />
              产物文件（{result.artifacts.length}）
            </button>
            <div className={`mt-1.5 flex-col gap-1.5 ${artifactsOpen ? "flex" : "hidden"}`}>
              {result.artifacts.map((f) => {
                const name = typeof f === "string" ? f : f.name;
                const size = typeof f === "string" ? undefined : f.size;
                const href = result.id ? archeApi.artifactUrl(result.id, name) : undefined;
                return (
                  <div
                    key={name}
                    className="flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5"
                  >
                    <span className="min-w-0 truncate font-mono text-[11px] text-slate-600">{name}</span>
                    <div className="flex shrink-0 items-center gap-2">
                      {typeof size === "number" && <span className="text-[10px] text-slate-400">{formatBytes(size)}</span>}
                      {href ? (
                        <a
                          href={href}
                          download={name}
                          className="inline-flex items-center gap-1 rounded bg-white px-2 py-0.5 text-[11px] font-medium text-[#14532d] ring-1 ring-inset ring-slate-200 transition hover:bg-[#f3f8f5]"
                        >
                          <Download className="size-3" /> 下载
                        </a>
                      ) : (
                        <span className="text-[10px] text-slate-300">无法下载（旧记录）</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 原始日志（折叠，高级排查用）—— 始终提供展开入口：即便本次未捕获到日志也明确说明,
            避免"诊断提示可展开原始日志、却根本没有展开处"的割裂。 */}
        <div>
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            className="flex items-center gap-1.5 text-xs font-medium text-slate-400 transition hover:text-slate-600"
          >
            <TerminalSquare className="size-3.5" />
            原始执行日志（高级）
            <ChevronDown className={`size-3.5 transition-transform ${showRaw ? "rotate-180" : ""}`} />
          </button>
          {showRaw && (
            <pre className="console-scroll mt-2 max-h-72 overflow-auto rounded-lg border border-slate-800 bg-slate-950 px-4 py-3 font-mono text-xs leading-relaxed text-slate-200">
              {rawLog
                ? rawLog
                : isRunning
                  ? "（运行刚开始，日志尚未产生 —— 稍候本页会自动刷新出实时进度与日志。）"
                  : `（本次记录未捕获到原始日志 —— stdout/stderr 为空。\n可能原因：运行被中断 / 子进程未产生输出。\n状态：${result.status ?? "unknown"}　退出码：${result.exitCode ?? "—"}）`}
            </pre>
          )}
        </div>
      </div>
    </section>
  );
}
