import type { RunSummary } from "../types";

/**
 * 解析 controller stdout 里的「工作流执行摘要」块，例如：
 *   📋 科学问题: ...
 *   📊 状态: success
 *   📈 阶段数: 4
 *   🎯 成功率: 100.00%
 * 解析不到就返回空对象（UI 退化为只展示诊断/原始日志）。
 */
export function parseSummary(stdout: string): RunSummary {
  const pick = (re: RegExp): string | undefined => stdout.match(re)?.[1]?.trim() || undefined;
  const num = (re: RegExp): number | undefined => {
    const v = pick(re);
    if (v === undefined) return undefined;
    const n = Number.parseFloat(v);
    return Number.isNaN(n) ? undefined : n;
  };
  return {
    question: pick(/科学问题[:：]\s*(.+)/),
    status: pick(/状态[:：]\s*([^\s]+)/),
    stages: num(/阶段数[:：]\s*(\d+)/),
    totalSteps: num(/总步骤[:：]\s*(\d+)/),
    succeededSteps: num(/成功步骤[:：]\s*(\d+)/),
    failedSteps: num(/失败步骤[:：]\s*(\d+)/),
    successRate: pick(/成功率[:：]\s*([\d.]+%)/),
    durationSeconds: num(/总耗时[:：]\s*([\d.]+)\s*秒/),
    errorMessage: pick(/❌\s*错误[:：]\s*(.+)/) || pick(/处理流程失败[:：]\s*(.+)/),
  };
}

export interface Diagnosis {
  tone: "ok" | "warn" | "error" | "running" | "cancelled";
  title: string;
  hints: string[];
}

/** 把原始日志里的常见问题翻译成用户能看懂的中文诊断（不暴露终端细节）。 */
export function diagnose(stdout: string, exitCode: number | null | undefined, status?: string | null): Diagnosis {
  // 运行中：后端记录标记 status='running'、exitCode 尚未产生（null）。绝不能按失败诊断 ——
  // 否则「返回首页再进入 / 刷新后回看」正在跑的 run 会因 exitCode!==0 被误判成「工作流未正常完成」。
  if (status === "running") {
    return { tone: "running", title: "工作流进行中 · 运行尚未结束", hints: [] };
  }
  if (status === "cancelled" || status === "canceled") {
    return { tone: "cancelled", title: "工作流已取消", hints: ["用户已停止本次运行。"] };
  }
  // 区分「真问题」(影响结果) 与「良性提示」(不影响核心计算结果)。
  const problems: string[] = [];
  const info: string[] = [];
  if (/Connection error|Empty reply|Connection refused|Max retries|ECONNREFUSED|Failed to establish/i.test(stdout)) {
    problems.push("无法连接模型服务：请确认右上角「模型服务配置」中的地址可达，且本机网络 / Tailnet 正常。");
  }
  if (/No module named 'numpy'|Agent模块不可用/.test(stdout)) {
    problems.push("Agent 运行依赖缺失，已回退到内置流程。");
  }
  if (/No such file or directory: 'papers'|paperscraper 不可用/.test(stdout)) {
    info.push("文献检索资源未配置，已跳过论文下载步骤（不影响后续推理）。");
  }
  if (/sentence-transformers 不可用/.test(stdout)) {
    info.push("语义检索模型未安装，已退化为关键词匹配。");
  }
  const hints = [...problems, ...info];
  // 良性提示(论文跳过 / 语义检索退化 等)不算「降级」——只有真问题(连接 / 依赖)才标 warn,
  // 否则真实计算成功的 run 会被一条「论文下载跳过」误标成「部分降级」。
  if (exitCode === 0 && problems.length === 0) return { tone: "ok", title: "工作流完成", hints };
  if (exitCode === 0) return { tone: "warn", title: "工作流完成（部分步骤已降级）", hints };
  return {
    tone: "error",
    title: "工作流未正常完成",
    hints: hints.length ? hints : ["执行过程中出现错误，可展开下方原始日志排查。"],
  };
}
