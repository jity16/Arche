import {
  Brain,
  Check,
  ChevronDown,
  Download,
  Eye,
  FileJson,
  FileText,
  FlaskConical,
  Lightbulb,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { type ComponentType, useEffect, useMemo, useState } from "react";
import { archeApi } from "../api";
import type { ArtifactFile, RunResult, StreamEvent, TimelineStep } from "../types";
import {
  ArtifactPreview,
  artifactName,
  artifactSize,
  collapseTimeline,
  formatBytes,
  stepArtifactName,
  stepInfo,
} from "./ResearchTimeline";

export type Step = "pending" | "running" | "done";
export interface RoundState {
  round: number;
  total?: number;
  planning: Step;
  execution: Step;
  reflection: Step;
  revised: boolean;
}
export interface LogLine {
  level: string; // info | warn | error
  message: string;
}
export interface LoopState {
  retrieval: Step;
  hypothesis: Step;
  rounds: RoundState[];
  logs: LogLine[];
  finished: boolean;
  active: string;
}

export function emptyLoop(): LoopState {
  return { retrieval: "pending", hypothesis: "pending", rounds: [], logs: [], finished: false, active: "" };
}

const stepStatus = (status: string): Step => (status === "started" ? "running" : "done");

/** 把后端真实事件归约进 LoopState（纯函数）。 */
export function applyEvent(prev: LoopState, e: StreamEvent): LoopState {
  if (e.type === "start") return emptyLoop();
  const s: LoopState = { ...prev, rounds: prev.rounds.map((r) => ({ ...r })) };

  if (e.type === "round") {
    if (!s.rounds.some((r) => r.round === e.round)) {
      s.rounds = [
        ...s.rounds,
        { round: e.round, total: e.total, planning: "pending", execution: "pending", reflection: "pending", revised: false },
      ];
    }
    s.active = `反馈闭环 · 轮次 ${e.round}/${e.total}`;
    return s;
  }
  if (e.type === "revise") {
    if (s.rounds.length) s.rounds[s.rounds.length - 1].revised = true;
    s.active = "根据反思修订工作流，进入下一轮";
    return s;
  }
  if (e.type === "log") {
    s.logs = [...prev.logs, { level: e.level || "info", message: e.message }].slice(-200);
    return s;
  }
  if (e.type === "done") {
    s.finished = true;
    s.active = e.exitCode === 0 ? "工作流完成" : "工作流结束（含错误）";
    return s;
  }
  if (e.type === "step") {
    const status = stepStatus(e.status);
    if (e.step === "retrieval_phase") {
      s.retrieval = status;
      s.active = "初始检索";
      return s;
    }
    if (e.step === "hypothesis_phase") {
      s.hypothesis = status;
      s.active = "初始假设";
      return s;
    }
    const ensureLast = (): RoundState => {
      if (s.rounds.length === 0) {
        s.rounds = [{ round: 1, planning: "pending", execution: "pending", reflection: "pending", revised: false }];
      }
      return s.rounds[s.rounds.length - 1];
    };
    const mRound = e.step.match(/reflection_phase_round_(\d+)/);
    if (mRound) {
      const n = Number(mRound[1]);
      let r = s.rounds.find((x) => x.round === n);
      if (!r) {
        r = { round: n, planning: "pending", execution: "pending", reflection: "pending", revised: false };
        s.rounds = [...s.rounds, r];
      }
      r.reflection = status;
      s.active = `轮次 ${n} · 反思`;
      return s;
    }
    if (e.step === "planner_phase") {
      const r = ensureLast();
      r.planning = status;
      s.active = `轮次 ${r.round} · 规划`;
      return s;
    }
    if (e.step === "execution_phase") {
      const r = ensureLast();
      r.execution = status;
      s.active = `轮次 ${r.round} · 执行`;
      return s;
    }
  }
  return s;
}

type NodeStatus = Step | "failed" | "waiting";

interface WorkflowNode {
  key: string;
  label: string;
  caption: string;
  meta: string;
  progress: string;
  status: NodeStatus;
  Icon: ComponentType<{ className?: string }>;
  stepName?: string;
  data?: Record<string, unknown>;
}

const FLOW_PHASES = ["检索", "假设", "规划", "执行", "反思"];

function statusText(status: NodeStatus): string {
  if (status === "done") return "完成";
  if (status === "running") return "进行中";
  if (status === "failed") return "失败";
  if (status === "waiting") return "等待任务";
  return "等待";
}

function statusClass(status: NodeStatus): { card: string; icon: string; badge: string; dot: string } {
  if (status === "done") {
    return {
      card: "border-[#b7d4c0] bg-[#f7fbf8] shadow-sm",
      icon: "bg-[#14532d] text-white",
      badge: "bg-white text-[#14532d] ring-[#b7d4c0]",
      dot: "bg-[#14532d]",
    };
  }
  if (status === "running") {
    return {
      card: "border-amber-300 bg-amber-50/60 shadow-[0_14px_34px_rgba(180,83,9,0.08)]",
      icon: "bg-white text-amber-700 ring-1 ring-inset ring-amber-200",
      badge: "bg-white text-amber-700 ring-amber-200",
      dot: "bg-amber-500",
    };
  }
  if (status === "failed") {
    return {
      card: "border-rose-200 bg-rose-50/60 shadow-sm",
      icon: "bg-rose-600 text-white",
      badge: "bg-white text-rose-700 ring-rose-200",
      dot: "bg-rose-500",
    };
  }
  if (status === "waiting") {
    return {
      card: "border-sky-200 bg-sky-50/50 shadow-sm",
      icon: "bg-white text-sky-700 ring-1 ring-inset ring-sky-200",
      badge: "bg-white text-sky-700 ring-sky-200",
      dot: "bg-sky-500",
    };
  }
  return {
    card: "border-slate-200 bg-white shadow-sm",
    icon: "bg-slate-50 text-slate-300 ring-1 ring-inset ring-slate-200",
    badge: "bg-slate-50 text-slate-400 ring-slate-200",
    dot: "bg-slate-300",
  };
}

function timelineStatus(status: string): NodeStatus {
  if (status === "completed") return "done";
  if (status === "failed") return "failed";
  if (status === "waiting_for_gaussian_jobs") return "waiting";
  return "running";
}

function stepCaption(step: string): string {
  if (step === "retrieval_phase") return "收集关键词、文献线索与化学上下文。";
  if (step === "hypothesis_phase") return "把检索材料整理成可计算的研究假设。";
  if (step === "planner_phase") return "选择工具、参数与执行顺序。";
  if (step === "execution_phase") return "运行计算工具并收集结构化结果。";
  if (/reflection_phase_round_\d+/.test(step)) return "检查结果，决定采纳、停止或修订。";
  return "记录该节点的阶段输出。";
}

function shortValue(v: unknown): string {
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v.length > 64 ? `${v.slice(0, 64)}...` : v;
  if (Array.isArray(v)) return `${v.length} 项`;
  if (v && typeof v === "object") return `${Object.keys(v as Record<string, unknown>).length} 字段`;
  return "—";
}

function summaryEntries(data?: Record<string, unknown>): Array<[string, string]> {
  if (!data) return [];
  return Object.entries(data)
    .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))
    .slice(0, 4)
    .map(([k, v]) => [k, shortValue(v)]);
}

function nodePhase(label: string): string {
  if (label.includes("检索")) return "检索";
  if (label.includes("假设")) return "假设";
  if (label.includes("规划")) return "规划";
  if (label.includes("执行")) return "执行";
  if (label.includes("反思")) return "反思";
  return "";
}

function phaseStatus(phase: string, nodes: WorkflowNode[]): NodeStatus {
  const matched = nodes.filter((n) => nodePhase(n.label) === phase);
  if (matched.some((n) => n.status === "failed")) return "failed";
  if (matched.some((n) => n.status === "running" || n.status === "waiting")) return matched.some((n) => n.status === "running") ? "running" : "waiting";
  if (matched.length > 0 && matched.every((n) => n.status === "done")) return "done";
  return "pending";
}

function statusNarrative(node: WorkflowNode): string {
  if (node.status === "running") return `${node.label}正在推进：${node.progress}`;
  if (node.status === "waiting") return `${node.label}已提交，等待外部任务返回结果。`;
  if (node.status === "failed") return `${node.label}出现异常，右侧工作区可查看相关文件。`;
  if (node.status === "done") return `${node.label}已完成，结果已进入后续阶段。`;
  return `${node.label}等待上一阶段完成。`;
}

function phaseProgressSegments(nodes: WorkflowNode[]): Array<{ label: string; status: NodeStatus }> {
  return FLOW_PHASES.map((phase) => ({ label: phase, status: phaseStatus(phase, nodes) }));
}

function SegmentedPhaseProgress({ nodes }: { nodes: WorkflowNode[] }) {
  return (
    <div className="mt-3">
      <div className="grid grid-cols-5 gap-1" aria-label="工作流五段进度">
        {phaseProgressSegments(nodes).map((segment) => {
          const tone = statusClass(segment.status);
          return (
            <div key={segment.label} className="min-w-0">
              <div
                className={`relative h-2 overflow-hidden rounded-full ring-1 ring-inset ${
                  segment.status === "done"
                    ? "bg-[#14532d] ring-[#14532d]/15"
                    : segment.status === "running"
                      ? "arche-flow-scan bg-amber-100 ring-amber-300"
                      : segment.status === "waiting"
                        ? "arche-flow-scan bg-sky-100 ring-sky-300"
                        : segment.status === "failed"
                          ? "bg-rose-500 ring-rose-300"
                          : "bg-slate-100 ring-slate-200"
                }`}
              />
              <div className="mt-1 flex min-w-0 items-center gap-1 text-[10px]">
                <span className={`size-1.5 shrink-0 rounded-full ${tone.dot}`} />
                <span className={`${segment.status === "pending" ? "text-slate-400" : "text-slate-600"} truncate`}>{segment.label}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function timelineNodes(timeline?: TimelineStep[]): WorkflowNode[] {
  return collapseTimeline(timeline || []).map((step, i) => {
    const { label, Icon } = stepInfo(step.step);
    const status = timelineStatus(step.status);
    const time = step.timestamp ? step.timestamp.slice(11, 19) : "历史记录";
    const entries = summaryEntries(step.data);
    return {
      key: `${step.step}-${step.timestamp ?? i}`,
      label,
      caption: stepCaption(step.step),
      meta: time,
      progress: entries.length ? entries.map(([k, v]) => `${k}: ${v}`).join(" · ") : statusText(status),
      status,
      Icon,
      stepName: step.step,
      data: step.data,
    };
  });
}

function liveNodes(state: LoopState, running: boolean): WorkflowNode[] {
  const latestLog = state.logs[state.logs.length - 1]?.message || "";
  const activeText = latestLog || state.active || "等待阶段事件";
  const make = (
    key: string,
    label: string,
    caption: string,
    meta: string,
    Icon: ComponentType<{ className?: string }>,
    status: Step,
    stepName?: string,
  ): WorkflowNode | null => {
    if (status === "pending") return null;
    return {
      key,
      label,
      caption,
      meta,
      Icon,
      status,
      stepName,
      progress: status === "running" ? activeText : "该节点已完成，产物将在结束后同步到工作区。",
    };
  };

  const nodes: Array<WorkflowNode | null> = [
    make("retrieval", "文献检索", "收集关键词、文献线索与化学上下文。", "初始阶段", Search, state.retrieval, "retrieval_phase"),
    make("hypothesis", "假设生成", "把检索材料整理成可计算的研究假设。", "初始阶段", Lightbulb, state.hypothesis, "hypothesis_phase"),
  ];
  for (const r of state.rounds) {
    const roundMeta = `轮次 ${r.round}${r.total ? ` / ${r.total}` : ""}${r.revised ? " · 已修订" : ""}`;
    nodes.push(make(`round-${r.round}-planning`, "计算规划", "选择工具、参数与执行顺序。", roundMeta, Brain, r.planning, "planner_phase"));
    nodes.push(make(`round-${r.round}-execution`, "工具执行", "运行计算工具并收集结构化结果。", roundMeta, FlaskConical, r.execution, "execution_phase"));
    nodes.push(
      make(`round-${r.round}-reflection`, "结果反思", "检查计算结果，决定采纳、停止或修订。", roundMeta, RefreshCw, r.reflection, `reflection_phase_round_${r.round}`),
    );
  }

  const concrete = nodes.filter(Boolean) as WorkflowNode[];
  if (concrete.length === 0 && running) {
    concrete.push({
      key: "boot",
      label: "初始化",
      caption: "建立运行记录并等待第一个阶段事件。",
      meta: "准备运行",
      progress: activeText,
      status: "running",
      Icon: Loader2,
    });
  }
  return concrete;
}

function WorkflowNodeCard({
  node,
  selected,
  onSelect,
}: {
  node: WorkflowNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const tone = statusClass(node.status);
  const entries = summaryEntries(node.data);
  const narrative = statusNarrative(node);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`group min-h-[124px] rounded-lg border px-3 py-3 text-left transition ${
        selected ? "ring-2 ring-[#14532d]/25" : "hover:-translate-y-0.5 hover:border-slate-300"
      } ${tone.card} ${node.status === "running" ? "arche-node-flow" : ""}`}
    >
      <div className="flex items-start gap-3">
        <div className={`flex size-8 shrink-0 items-center justify-center rounded-md ${tone.icon}`}>
          {node.status === "done" ? <Check className="size-4" /> : <node.Icon className={`size-4 ${node.status === "running" ? "arche-pulse" : ""}`} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-900">{node.label}</span>
            <span className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${tone.badge}`}>
              {statusText(node.status)}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-slate-400">
            <span className={`size-1.5 rounded-full ${tone.dot}`} />
            <span className="truncate">{node.meta}</span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">{node.caption}</p>
          <p className="mt-2 max-h-10 overflow-hidden text-[11px] leading-relaxed text-slate-500">{narrative}</p>
          {entries.length > 0 && (
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              {entries.slice(0, 2).map(([k, v]) => (
                <div key={k} className="min-w-0 rounded-md bg-white/70 px-2 py-1 ring-1 ring-inset ring-slate-200/70">
                  <div className="truncate text-[10px] text-slate-400">{k}</div>
                  <div className="truncate text-[11px] text-slate-600">{v}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}

function PreviewDialog({
  runId,
  fileName,
  stepName,
  onClose,
}: {
  runId: string;
  fileName: string;
  stepName: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6 backdrop-blur-sm" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${fileName} 预览`}
        className="flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_30px_80px_rgba(15,23,42,0.22)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-[#fbfcfb] px-4 py-3">
          <div className="min-w-0">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">文件预览</div>
            <div className="mt-0.5 truncate font-mono text-xs text-slate-800">{fileName}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex size-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="关闭预览"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="console-scroll min-h-0 flex-1 overflow-auto p-4">
          <ArtifactPreview runId={runId} name={fileName} stepName={stepName} />
        </div>
      </div>
    </div>
  );
}

function WorkflowWorkspace({
  result,
  selectedNode,
  running,
  workspaceCollapsed,
  setWorkspaceCollapsed,
}: {
  result?: RunResult | null;
  selectedNode?: WorkflowNode;
  running: boolean;
  workspaceCollapsed: boolean;
  setWorkspaceCollapsed: (collapsed: boolean) => void;
}) {
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  const artifacts = result?.artifacts || [];
  const runId = result?.id || "";
  const selectedArtifact = selectedNode?.stepName ? stepArtifactName(selectedNode.stepName) : null;
  const files = [
    ...artifacts.filter((f) => selectedArtifact && artifactName(f) === selectedArtifact),
    ...artifacts.filter((f) => !selectedArtifact || artifactName(f) !== selectedArtifact),
  ];
  const selectedEntries = summaryEntries(selectedNode?.data);
  const previewStepName = selectedNode?.stepName || "";

  useEffect(() => {
    if (previewFile && !files.some((f) => artifactName(f) === previewFile)) setPreviewFile(null);
  }, [files, previewFile]);

  if (workspaceCollapsed) {
    return (
      <aside className="flex min-h-[13rem] items-start justify-center rounded-lg border border-slate-200 bg-white px-1.5 py-2 shadow-[0_18px_50px_rgba(15,23,42,0.06)] xl:sticky xl:top-3">
        <button
          type="button"
          onClick={() => setWorkspaceCollapsed(false)}
          aria-label={workspaceCollapsed ? "展开工作区" : "收起工作区"}
          title="展开工作区"
          className="flex size-8 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-[#14532d] focus:outline-none focus:ring-2 focus:ring-[#14532d]/25"
        >
          <PanelRightOpen className="size-4" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="min-w-0 rounded-lg border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.06)] xl:sticky xl:top-3">
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-[#fbfcfb] px-4 py-3">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-[#14532d]">工作区</div>
          <div className="mt-0.5 truncate text-sm font-semibold text-slate-900">{selectedNode?.label || "选择一个节点"}</div>
          <div className="mt-1 text-[11px] text-slate-400">{files.length > 0 ? `${files.length} 个文件` : "文件同步区"}</div>
        </div>
        <button
          type="button"
          onClick={() => setWorkspaceCollapsed(true)}
          aria-label={workspaceCollapsed ? "展开工作区" : "收起工作区"}
          title="收起工作区"
          className="flex size-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-[#14532d] focus:outline-none focus:ring-2 focus:ring-[#14532d]/25"
        >
          <PanelRightClose className="size-4" />
        </button>
      </div>

      <div className="space-y-4 p-4">
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400">当前节点</div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
              {selectedNode ? <selectedNode.Icon className="size-4 text-[#14532d]" /> : <RefreshCw className="size-4 text-slate-300" />}
              <span className="min-w-0 truncate">{selectedNode?.label || "暂无节点"}</span>
              {selectedNode && (
                <span className={`ml-auto rounded-full px-2 py-0.5 text-[10px] ring-1 ring-inset ${statusClass(selectedNode.status).badge}`}>
                  {statusText(selectedNode.status)}
                </span>
              )}
            </div>
            <div className="mt-1 text-xs leading-relaxed text-slate-500">
              {selectedNode?.progress || (running ? "等待后端写入第一个阶段事件。" : "选择左侧节点查看对应文件。")}
            </div>
            {selectedEntries.length > 0 && (
              <div className="mt-2 grid gap-1">
                {selectedEntries.map(([k, v]) => (
                  <div key={k} className="flex min-w-0 justify-between gap-2 rounded-md bg-white px-2 py-1 text-[11px] ring-1 ring-inset ring-slate-100">
                    <span className="truncate text-slate-400">{k}</span>
                    <span className="max-w-[58%] truncate text-right font-mono text-slate-600">{v}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">文件</div>
            {selectedArtifact && <span className="rounded bg-[#eef7f1] px-1.5 py-0.5 text-[9px] font-medium text-[#14532d]">当前节点优先</span>}
          </div>
          {files.length > 0 && runId ? (
            <div className="console-scroll max-h-[22rem] space-y-1 overflow-auto pr-1">
              {files.map((file) => {
                const name = artifactName(file);
                const size = artifactSize(file);
                const stageFile = selectedArtifact === name;
                return (
                  <div
                    key={name}
                    className={`flex min-w-0 items-center gap-2 rounded-lg border px-2 py-2 ${
                      stageFile ? "border-[#b7d4c0] bg-[#f7fbf8]" : "border-slate-200 bg-white"
                    }`}
                  >
                    {name.endsWith(".json") ? <FileJson className="size-4 shrink-0 text-[#14532d]" /> : <FileText className="size-4 shrink-0 text-slate-400" />}
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-[11px] text-slate-700">{name}</div>
                      <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
                        {stageFile && <span>节点文件</span>}
                        {size !== undefined && <span>{formatBytes(size)}</span>}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setPreviewFile(name)}
                      className="flex size-7 shrink-0 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-[#14532d]"
                      title="预览"
                      aria-label={`预览 ${name}`}
                    >
                      <Eye className="size-4" />
                    </button>
                    <a
                      href={archeApi.artifactUrl(runId, name)}
                      download={name}
                      className="flex size-7 shrink-0 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-[#14532d]"
                      title="下载"
                      aria-label={`下载 ${name}`}
                    >
                      <Download className="size-4" />
                    </a>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-8 text-center text-xs leading-relaxed text-slate-400">
              {running ? "阶段文件生成后会同步到这里。" : "暂无产物文件。"}
            </div>
          )}
        </div>
      </div>

      {previewFile && runId && (
        <PreviewDialog runId={runId} fileName={previewFile} stepName={previewStepName} onClose={() => setPreviewFile(null)} />
      )}
    </aside>
  );
}

export function AgentLoop({ state, running, result }: { state: LoopState; running: boolean; result?: RunResult | null }) {
  const [showLog, setShowLog] = useState(false);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const persistedNodes = useMemo(() => timelineNodes(result?.timeline), [result?.timeline]);
  const nodes = persistedNodes.length > 0 ? persistedNodes : liveNodes(state, running);
  const nodeKeys = nodes.map((n) => n.key).join("|");
  const preferredKey = nodes.find((n) => n.status === "running" || n.status === "waiting")?.key || nodes[nodes.length - 1]?.key || "";
  const [selectedKey, setSelectedKey] = useState(preferredKey);

  useEffect(() => {
    setSelectedKey((prev) => (prev && nodes.some((n) => n.key === prev) ? prev : preferredKey));
  }, [nodeKeys, preferredKey]);

  const selectedNode = nodes.find((n) => n.key === selectedKey) || nodes[nodes.length - 1];
  const done = nodes.filter((n) => n.status === "done").length;
  const failed = nodes.filter((n) => n.status === "failed").length;
  const current = nodes.find((n) => n.status === "running" || n.status === "waiting");
  const overall = failed > 0 ? "需要检查" : current ? current.label : nodes.length > 0 && (state.finished || result) ? "完成" : running ? "准备中" : "待开始";

  return (
    <section className={`grid gap-4 ${workspaceCollapsed ? "xl:grid-cols-[minmax(0,1fr)_48px]" : "xl:grid-cols-[minmax(0,1fr)_360px]"}`}>
      <div className="min-w-0 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,0.06)]">
        <div className="border-b border-slate-200 bg-[#fbfcfb] px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-[#14532d]">整体进度</div>
              <h3 className="mt-0.5 text-sm font-semibold text-slate-900">工作流</h3>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="rounded-full bg-white px-2 py-1 ring-1 ring-inset ring-slate-200">{overall}</span>
              {nodes.length > 0 && <span className="font-mono text-[11px]">{done}/{nodes.length}</span>}
            </div>
          </div>
          <SegmentedPhaseProgress nodes={nodes} />
        </div>

        <div className="space-y-3 p-3">
          {nodes.length > 0 ? (
            <div className="grid gap-2 md:grid-cols-2 2xl:grid-cols-3">
              {nodes.map((node) => (
                <WorkflowNodeCard key={node.key} node={node} selected={node.key === selectedNode?.key} onSelect={() => setSelectedKey(node.key)} />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-400">
              提交研究问题后，节点会按执行顺序出现在这里。
            </div>
          )}

          {state.logs.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setShowLog((v) => !v)}
                className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-600"
              >
                <ChevronDown className={`size-3 transition-transform ${showLog ? "rotate-180" : ""}`} /> 实时活动 ({state.logs.length})
                {state.logs.some((l) => l.level === "warn") && (
                  <span className="ml-1 rounded bg-amber-50 px-1 text-[9px] font-medium text-amber-700 ring-1 ring-inset ring-amber-500/20">
                    含降级 {state.logs.filter((l) => l.level === "warn").length}
                  </span>
                )}
              </button>
              {showLog && (
                <div className="console-scroll mt-1 max-h-48 overflow-auto rounded-lg bg-slate-950 px-3 py-2 font-mono text-[10px] leading-relaxed">
                  {state.logs.map((l, i) => (
                    <div
                      key={`${i}-${l.message.slice(0, 12)}`}
                      className={l.level === "error" ? "text-rose-300" : l.level === "warn" ? "text-amber-300" : "text-slate-300"}
                    >
                      {l.level === "warn" ? "WARN " : l.level === "error" ? "ERR " : "INFO "}
                      {l.message}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <WorkflowWorkspace
        result={result}
        selectedNode={selectedNode}
        running={running}
        workspaceCollapsed={workspaceCollapsed}
        setWorkspaceCollapsed={setWorkspaceCollapsed}
      />
    </section>
  );
}
