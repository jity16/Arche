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
    <header className="relative z-20 shrink-0 border-b border-[#1d3b2d] bg-[#0b1f17] shadow-[0_18px_42px_rgba(11,31,23,0.18)]">
      <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-2.5 sm:px-6 lg:px-8">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-md border border-white/15 bg-white text-[#0b1f17] shadow-sm">
            <BenzeneLogo className="size-5" />
          </div>
          <h1 className="truncate text-lg font-semibold tracking-tight text-white">ARCHE</h1>
        </div>
        <div className="flex items-center gap-3">
          <span className="inline-flex h-8 items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 text-xs font-medium text-emerald-50/80">
            <span className={`size-2 rounded-full ${dot.color} ${state === "loading" ? "arche-pulse" : ""}`} />
            <Activity className="size-3.5 text-emerald-50/45" />
            {dot.label}
          </span>
          <button
            type="button"
            onClick={onOpenConfig}
            title="模型服务配置"
            aria-label="模型服务配置"
            className="flex size-8 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-emerald-50/75 transition hover:bg-white/10 hover:text-white"
          >
            <Settings2 className="size-4" />
          </button>
        </div>
      </div>
    </header>
  );
}
