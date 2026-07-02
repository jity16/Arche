import { AlertTriangle, CheckCircle2, ShieldAlert } from "lucide-react";
import type { ArcheRunResult } from "../types";

type Level = "full" | "partial" | "untrustworthy";

function num(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}
function pct(v: number): string {
  return v <= 1 ? `${(v * 100).toFixed(0)}%` : String(v);
}
function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

/** 从持久化的结构化结果(bounded_closed_loop_result.json = result）算运行可信度。
 *  信号:专家模型是否回退/规则启发、执行成功率、失败步骤、结论类型/状态。 */
export function computeHealth(sci: ArcheRunResult): { level: Level; reasons: string[] } {
  const ss = rec(sci.shared_state);
  const audit = rec(ss.expert_backend_audit_summary);
  const fc = rec(sci.final_conclusion);
  const wo = rec(fc.workflow_outcome);
  const reasons: string[] = [];
  let level: Level = "full";

  const runMode = String(audit.expert_run_mode ?? "");
  const fallback = ss.fallback_triggered === true;
  const successRate = num(wo.execution_success_rate);
  const concType = String(fc.conclusion_type ?? "");
  const outcome = String(wo.workflow_outcome ?? "");
  const status = String(sci.status ?? "");
  const failedSteps = Array.isArray(wo.failed_steps) ? wo.failed_steps.length : num(wo.failed_steps) ?? 0;

  // —— 不可信信号(任一即降到 untrustworthy) ——
  if (runMode.includes("rule_based") || runMode.includes("deepseek_fallback")) {
    level = "untrustworthy";
    reasons.push("化学专家分析回退到规则启发/备用模型,结论可信度低");
  }
  if (["failed", "error"].includes(concType) || outcome === "failed" || ["failed", "error"].includes(status)) {
    level = "untrustworthy";
    reasons.push("工作流未得出可靠结论(failed)");
  }
  if (successRate != null && successRate < 0.7) {
    level = "untrustworthy";
    reasons.push(`执行成功率偏低(${pct(successRate)})`);
  }

  // —— 降级信号(仅在尚为 full 时下调到 partial) ——
  if (level === "full") {
    if (fallback) {
      level = "partial";
      reasons.push("触发了专家模型回退");
    }
    if (successRate != null && successRate < 0.9) {
      level = "partial";
      reasons.push(`执行成功率 ${pct(successRate)}`);
    }
    if (failedSteps > 0) {
      level = "partial";
      reasons.push(`${failedSteps} 个步骤失败(部分经修订恢复)`);
    }
    if (concType === "provisional" || outcome === "partially_supported") {
      level = "partial";
      reasons.push("结论为暂定 / 部分支持");
    }
  }
  return { level, reasons };
}

const TONE: Record<Level, { cls: string; Icon: typeof CheckCircle2; title: string }> = {
  full: {
    cls: "border-teal-200 bg-teal-50/70 text-teal-800",
    Icon: CheckCircle2,
    title: "运行完成 · 结果可信",
  },
  partial: {
    cls: "border-amber-200 bg-amber-50/70 text-amber-800",
    Icon: AlertTriangle,
    title: "部分步骤未完成 · 结论供参考",
  },
  untrustworthy: {
    cls: "border-rose-200 bg-rose-50/70 text-rose-800",
    Icon: ShieldAlert,
    title: "结果可信度较低 · 请谨慎参考",
  },
};

/** 结果页顶部运行健康度横幅:一眼判断这份计算化学结论可不可信。 */
export function RunHealthBanner({ sci }: { sci: ArcheRunResult | null }) {
  if (!sci) return null;
  const { level, reasons } = computeHealth(sci);
  const tone = TONE[level];
  return (
    <div className={`flex gap-2.5 rounded-xl border px-4 py-2.5 ${tone.cls}`}>
      <tone.Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold">{tone.title}</div>
        {reasons.length > 0 && (
          <ul className="mt-1 space-y-0.5">
            {reasons.map((r) => (
              <li key={r} className="flex gap-1.5 text-[11px] opacity-90">
                <span>·</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
