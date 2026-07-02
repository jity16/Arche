import { Brain, Check, ChevronDown, FlaskConical, Lightbulb, RefreshCw, RotateCcw, Search } from "lucide-react";
import { type ComponentType, useState } from "react";
import type { StreamEvent } from "../types";

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

function Node({ label, Icon, status }: { label: string; Icon: ComponentType<{ className?: string }>; status: Step }) {
  const tone =
    status === "done"
      ? "border-teal-500 bg-teal-500 text-white"
      : status === "running"
        ? "border-cyan-400 bg-cyan-50 text-cyan-700"
        : "border-slate-200 bg-white text-slate-300";
  return (
    <div className="flex flex-col items-center gap-1 text-center">
      <div className={`relative flex size-10 items-center justify-center rounded-full border-2 transition-colors duration-300 ${tone}`}>
        {status === "done" ? <Check className="size-5" /> : <Icon className={`size-4 ${status === "running" ? "arche-pulse" : ""}`} />}
        {status === "running" && (
          <span className="absolute inset-[-3px] animate-spin rounded-full border-2 border-cyan-400 border-t-transparent" />
        )}
      </div>
      <div className="leading-tight">
        <div className={`text-xs font-semibold ${status === "pending" ? "text-slate-400" : "text-slate-700"}`}>{label}</div>
      </div>
    </div>
  );
}

const Connector = ({ on }: { on: boolean }) => (
  <div className="mx-1 mt-5 h-0.5 w-4 shrink-0 rounded-full" style={{ background: on ? "#14b8a6" : "#e2e8f0" }} />
);

export function AgentLoop({ state, running }: { state: LoopState; running: boolean }) {
  const [showLog, setShowLog] = useState(false);
  const idle = !running && !state.finished && state.retrieval === "pending";

  return (
    <div>
      {/* 初始阶段 */}
      <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-slate-400">初始阶段</div>
      <div className="flex items-start">
        <Node label="检索" Icon={Search} status={state.retrieval} />
        <Connector on={state.retrieval === "done"} />
        <Node label="假设" Icon={Lightbulb} status={state.hypothesis} />
      </div>

      {/* 反馈闭环 */}
      <div className="mt-5 flex items-center gap-2">
        <div className="text-[10px] font-medium uppercase tracking-wide text-slate-400">反馈闭环</div>
        <RotateCcw className="size-3 text-teal-500" />
        {state.rounds.length > 0 && <span className="text-[10px] text-slate-400">共 {state.rounds.length} 轮</span>}
      </div>

      {state.rounds.length === 0 ? (
        <div className="mt-2 rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-xs text-slate-400">
          {idle ? "提交问题后，规划→执行→反思将按真实轮次在此展开" : "等待进入反馈循环…"}
        </div>
      ) : (
        <div className="mt-2 space-y-2">
          {state.rounds.map((r) => (
            <div key={r.round} className="rounded-xl border border-slate-200 bg-white/70 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-600">
                  轮次 {r.round}
                  {r.total ? ` / ${r.total}` : ""}
                </span>
                {r.revised && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700 ring-1 ring-inset ring-amber-500/20">
                    <RotateCcw className="size-3" /> 已修订
                  </span>
                )}
              </div>
              <div className="flex items-start">
                <Node label="规划" Icon={Brain} status={r.planning} />
                <Connector on={r.planning === "done"} />
                <Node label="执行" Icon={FlaskConical} status={r.execution} />
                <Connector on={r.execution === "done"} />
                <Node label="反思" Icon={RefreshCw} status={r.reflection} />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 当前活动 */}
      <p className={`mt-3 text-center text-xs ${running ? "text-cyan-600" : state.finished ? "text-teal-600" : "text-slate-400"}`}>
        {state.active || (idle ? "等待提交研究问题" : "")}
        {running && <span className="arche-pulse"> ●</span>}
      </p>

      {/* 实时活动日志：info(进度) / warn(降级·模拟·回退) / error 按级着色；运行中默认展开。 */}
      {state.logs.length > 0 && (
        <div className="mt-2">
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
            <div className="console-scroll mt-1 max-h-48 overflow-auto rounded-lg bg-slate-900 px-3 py-2 font-mono text-[10px] leading-relaxed">
              {state.logs.map((l, i) => (
                <div
                  key={`${i}-${l.message.slice(0, 12)}`}
                  className={l.level === "error" ? "text-rose-300" : l.level === "warn" ? "text-amber-300" : "text-slate-300"}
                >
                  {l.level === "warn" ? "⚠ " : l.level === "error" ? "✗ " : "· "}
                  {l.message}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
