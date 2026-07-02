import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
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

/** P1-B：把结论 + 设置导出为 Markdown，便于贴进组会/SI/论文。 */
function toMarkdown(result: RunResult, summary: RunSummary, sci: ArcheRunResult | null): string {
  const lines: string[] = ["# ARCHE 计算化学工作流结果"];
  const q = summary.question || sci?.scientific_question || "";
  if (q) lines.push(`\n**研究问题**：${q}`);
  const c = sci?.final_conclusion;
  if (c?.conclusion_summary) lines.push(`\n## 结论\n${c.conclusion_summary}`);
  const findings = (c?.key_findings ?? []).map(valText).filter(Boolean);
  if (findings.length) lines.push(`\n## 关键发现\n${findings.map((f) => `- ${f}`).join("\n")}`);
  const steps = (c?.recommended_next_steps ?? []).map(valText).filter(Boolean);
  if (steps.length) lines.push(`\n## 建议后续步骤\n${steps.map((s) => `- ${s}`).join("\n")}`);
  const ctx = sci?.shared_state?.chemistry_context as Record<string, unknown> | undefined;
  if (ctx) {
    const entries = Object.entries(ctx).filter(([, v]) => v != null && v !== "" && typeof v !== "object");
    if (entries.length) lines.push(`\n## 计算设置\n${entries.map(([k, v]) => `- ${k}: ${valText(v)}`).join("\n")}`);
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

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <div className="mb-1 text-[11px] font-medium text-slate-500">{title}</div>
      <ul className="space-y-1 text-sm text-slate-700">
        {items.map((it) => (
          <li key={it} className="flex gap-1.5">
            <span className="mt-1 size-1 shrink-0 rounded-full bg-teal-500" />
            <MathText text={it} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 「科学结论」区：渲染 controller 真实产出的最终结论 + 计算设置。 */
function ConclusionSection({ sci }: { sci: ArcheRunResult }) {
  const c = sci.final_conclusion ?? {};
  const ctx = (sci.shared_state?.chemistry_context ?? {}) as Record<string, unknown>;
  const ctxEntries = Object.entries(ctx).filter(([, v]) => v != null && v !== "" && typeof v !== "object");
  const findings = (c.key_findings ?? []).map(valText).filter(Boolean);
  const steps = (c.recommended_next_steps ?? []).map(valText).filter(Boolean);
  const issues = (c.unresolved_issues ?? []).map(valText).filter(Boolean);
  const confidence = typeof c.confidence === "number" ? c.confidence : undefined;
  const summary = c.conclusion_summary;
  if (!summary && findings.length === 0 && steps.length === 0 && issues.length === 0 && ctxEntries.length === 0) return null;

  return (
    <div className="rounded-xl border border-teal-200 bg-teal-50/40 p-4">
      <div className="mb-2 flex items-center gap-1.5">
        <Lightbulb className="size-4 text-teal-600" />
        <span className="text-xs font-semibold text-teal-700">科学结论</span>
        {c.conclusion_type && CONCLUSION_TYPE_LABEL[String(c.conclusion_type)] && (
          <span className="rounded bg-teal-100 px-1.5 py-0.5 text-[10px] text-teal-700">
            {CONCLUSION_TYPE_LABEL[String(c.conclusion_type)]}
          </span>
        )}
        {confidence !== undefined && (
          <span className="ml-auto text-[11px] text-slate-500">置信度 {Math.round(confidence * 100)}%</span>
        )}
      </div>
      {summary ? (
        <MathText text={summary} className="block text-sm leading-relaxed text-slate-800" />
      ) : (
        <p className="text-sm text-slate-400">本次未生成明确结论（多为模型不可达 / 降级运行）。</p>
      )}
      <List title="关键发现" items={findings} />
      <List title="建议后续步骤" items={steps} />
      <List title="未决问题" items={issues} />
      {ctxEntries.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 flex items-center gap-1 text-[11px] font-medium text-slate-500">
            <ListChecks className="size-3" /> 计算设置
          </div>
          <div className="grid grid-cols-1 gap-x-5 gap-y-0.5 text-xs sm:grid-cols-2">
            {ctxEntries.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2 border-b border-teal-100/70 py-0.5">
                <span className="text-slate-400">{k}</span>
                <span className="font-mono text-slate-700">{valText(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const TONE = {
  ok: { bar: "bg-teal-50", text: "text-teal-700", Icon: CheckCircle2 },
  warn: { bar: "bg-amber-50", text: "text-amber-700", Icon: AlertTriangle },
  error: { bar: "bg-rose-50", text: "text-rose-700", Icon: XCircle },
  running: { bar: "bg-sky-50", text: "text-sky-700", Icon: Loader2 },
} as const;

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-center">
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
  accept: "bg-teal-100 text-teal-700",
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
    <div className="overflow-hidden rounded-xl border border-slate-200">
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
                    <span key={k} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
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
                  <div key={`${source}-${i}`} className="mb-1.5 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2">
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

export function ResultPanel({ result, error }: { result: RunResult | null; error: string | null }) {
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showArtifacts, setShowArtifacts] = useState(false);

  if (error) {
    return (
      <section className="overflow-hidden rounded-2xl border border-rose-200 bg-white shadow-sm">
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
    <section className="flex max-h-[85vh] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm shadow-slate-200/50">
      {/* 状态横幅（固定在顶部，不随内容滚动） */}
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

      {/* 内容区是唯一滚动容器：圆角裁剪在静止的 section 上、滚动在这个无圆角的内容区上 ——
          杜绝"双层圆角嵌套滚动导致边角白闪"。overscroll-contain 阻断滚动链；console-scroll 预留滚动条槽。 */}
      <div className="console-scroll min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-5 py-4 transform-gpu [contain:paint]">
        {/* 运行中回看：结论/时间线尚未落盘，明确告知仍在进行、本页会自动收敛到最终结果。 */}
        {isRunning && (
          <div className="flex items-center gap-2 rounded-xl border border-sky-200 bg-sky-50/60 px-4 py-3 text-sm text-sky-700">
            <Loader2 className="size-4 shrink-0 animate-spin" />
            <span>运行仍在进行中，完成后本页会自动刷新为最终结果。</span>
          </div>
        )}

        {/* 运行健康度:第一眼判断这份结论可不可信（含降级/模拟/失败则醒目提示） */}
        <RunHealthBanner sci={sci} />

        {/* 研究问题不在此重复展示 —— 详情页顶部已置顶问题卡片。 */}

        {sci && <ConclusionSection sci={sci} />}
        {sci && <ProcessDetails sci={sci} />}

        {/* 研究过程时间线：controller multiagent_log.json 的逐步事件，实时完成态与历史回看共用。
            不依赖 sci —— 即便失败/无最终结论，也能看清整个过程卡/错在哪一步。 */}
        <ResearchTimeline timeline={result.timeline} runId={result.id} artifacts={result.artifacts} />

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
          <div className={`rounded-xl border px-4 py-3 ${diag.tone === "error" ? "border-rose-200 bg-rose-50/60" : "border-amber-200 bg-amber-50/60"}`}>
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
        {result.artifacts && result.artifacts.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setShowArtifacts((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400 transition hover:text-slate-600"
            >
              <ChevronDown className={`size-3.5 transition-transform ${showArtifacts ? "rotate-0" : "-rotate-90"}`} />
              产物文件（{result.artifacts.length}）
            </button>
            <div className={`mt-1.5 flex-col gap-1.5 ${showArtifacts ? "flex" : "hidden"}`}>
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
                          className="inline-flex items-center gap-1 rounded bg-white px-2 py-0.5 text-[11px] font-medium text-teal-700 ring-1 ring-inset ring-slate-200 transition hover:bg-teal-50"
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
            <pre className="console-scroll mt-2 max-h-72 overflow-auto rounded-xl border border-slate-800 bg-slate-900 px-4 py-3 font-mono text-xs leading-relaxed text-slate-200">
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
