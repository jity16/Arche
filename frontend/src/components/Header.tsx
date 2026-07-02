import { Activity, Settings2 } from "lucide-react";
import type { AgentInfo, HealthInfo } from "../types";
import { BenzeneLogo } from "./Logo";

type HealthState = "ok" | "down" | "loading";

function healthState(health: HealthInfo | null, error: boolean): HealthState {
  if (error) return "down";
  if (!health) return "loading";
  return health.status === "ok" ? "ok" : "down";
}

const DOT: Record<HealthState, { color: string; label: string }> = {
  ok: { color: "bg-emerald-500", label: "在线" },
  down: { color: "bg-rose-500", label: "离线" },
  loading: { color: "bg-amber-400", label: "连接中" },
};

export function Header({
  info,
  health,
  healthError,
  onOpenConfig,
}: {
  info: AgentInfo | null;
  health: HealthInfo | null;
  healthError: boolean;
  onOpenConfig: () => void;
}) {
  const state = healthState(health, healthError);
  const dot = DOT[state];

  return (
    <header className="border-b border-slate-200/80 bg-white/85 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-4 px-6 py-4">
        <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-teal-500 to-cyan-600 text-white shadow-sm shadow-teal-600/30">
          <BenzeneLogo className="size-7" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold tracking-tight text-slate-900">ARCHE</h1>
            <span className="rounded-full bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-700 ring-1 ring-inset ring-teal-600/20">
              应用智能体
            </span>
            {info?.version ? (
              <span
                title="当前部署的镜像版本"
                className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[11px] font-medium text-slate-500 ring-1 ring-inset ring-slate-200"
              >
                v{info.version}
              </span>
            ) : null}
          </div>
          <p className="truncate text-xs text-slate-500">
            计算化学多智能体 · 检索 → 假设 → 规划 → 执行 → 反思
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-2 rounded-md bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200">
            <span className={`size-2 rounded-full ${dot.color} ${state === "loading" ? "arche-pulse" : ""}`} />
            <Activity className="size-3.5 text-slate-400" />
            {dot.label}
          </span>
          <button
            type="button"
            onClick={onOpenConfig}
            title="模型服务配置"
            aria-label="模型服务配置"
            className="rounded-md p-1.5 text-slate-400 ring-1 ring-inset ring-slate-200 transition hover:bg-slate-100 hover:text-slate-600"
          >
            <Settings2 className="size-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
