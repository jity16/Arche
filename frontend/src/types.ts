export interface AgentInfo {
  name: string;
  kind: string;
  description: string;
  entry: string;
  /** 当前部署的镜像版本（构建时 --build-arg ARCHE_VERSION 注入；缺失时后端返回 "unknown"）。 */
  version?: string;
}

export interface HealthInfo {
  status: string;
  agent: string;
}

/** controller 最终结论（bounded_closed_loop_result.json 的 final_conclusion）。 */
export interface FinalConclusion {
  scientific_question?: string;
  conclusion_type?: string;
  conclusion_summary?: string;
  selected_strategy?: unknown;
  workflow_outcome?: Record<string, unknown>;
  key_findings?: Array<({ summary?: string } & Record<string, unknown>) | string>;
  unresolved_issues?: Array<string | Record<string, unknown>>;
  recommended_next_steps?: string[];
  final_status?: string;
  final_decision?: string;
  total_reflection_rounds?: number;
  confidence?: number;
}

/** controller 写入的最终结构化结果（bounded_closed_loop_result.json）。 */
export interface ArcheRunResult {
  scientific_question?: string;
  workflow_version?: string;
  status?: string;
  final_conclusion?: FinalConclusion;
  shared_state?: { chemistry_context?: Record<string, unknown> } & Record<string, unknown>;
  [k: string]: unknown;
}

/** 持久化产物文件（可下载）。旧记录里 artifacts 可能是纯文件名字符串，前端需兼容两种。 */
export interface ArtifactFile {
  name: string;
  size: number;
}

/** 全过程时间线的一步：来自 controller 的 multiagent_log.json（按时间顺序的事件）。
 *  step 形如 retrieval_phase / hypothesis_phase / planner_phase / execution_phase / reflection_phase_round_N；
 *  status 为 started | completed | failed | waiting_for_gaussian_jobs；data 为该步的结构化科学内容。 */
export interface TimelineStep {
  timestamp?: string;
  step: string;
  status: string;
  data?: Record<string, unknown>;
}

/** /api/run 的返回（也可直接喂给 ResultPanel）。 */
export interface RunResult {
  id?: string;
  createdAt?: number;
  exitCode: number;
  status?: string | null;
  result?: ArcheRunResult | null;
  timeline?: TimelineStep[];
  artifacts?: Array<string | ArtifactFile>;
  stdout: string;
  stderr: string;
}

/** /api/runs/<id> 的完整记录（含 question）。 */
export interface RunRecord extends RunResult {
  id: string;
  createdAt: number;
  question: string;
}

/** /api/runs 列表项（轻量，无 stdout/stderr）。 */
export interface RunListItem {
  id: string;
  createdAt: number;
  question: string;
  exitCode: number | null;
  status: string | null;
}

/** 从 controller stdout 的「工作流执行摘要」里解析出的结构化字段。 */
export interface RunSummary {
  question?: string;
  status?: string;
  stages?: number;
  totalSteps?: number;
  succeededSteps?: number;
  failedSteps?: number;
  successRate?: string;
  durationSeconds?: number;
  errorMessage?: string;
}

/** /api/run/stream 推送的实时事件（NDJSON，每行一个）。 */
export type StreamEvent =
  | { type: "start"; id: string; createdAt: number; question: string }
  | { type: "step"; step: string; status: "started" | "completed" }
  | { type: "round"; round: number; total: number }
  | { type: "revise" }
  | { type: "log"; level: string; message: string }
  | {
      type: "done";
      id: string;
      createdAt: number;
      exitCode: number;
      status: string | null;
      result?: ArcheRunResult | null;
      timeline?: TimelineStep[];
      artifacts?: Array<string | ArtifactFile>;
      stdout: string;
      stderr: string;
    };

/** /api/model-status —— 模型端点连通性探活（不进 /healthz，K8s liveness 要快）。 */
export interface ModelStatus {
  ok: boolean;
  model: { configured: boolean; reachable: boolean | null; baseUrl?: string | null };
}

/** /api/config —— 运行时可在右上角弹窗里替换的配置（密钥类仅返回掩码/是否已设置）。 */
export interface ArcheConfig {
  enabled: boolean;
  baseUrl: string;
  model: string;
  apiKeyHeader: string;
  apiKeySet: boolean;
  apiKeyMasked: string;
  // ingress 网关 Basic Auth 凭证（AK/SK，等价 curl -u）。
  ingressAkSet: boolean;
  ingressAkMasked: string;
  ingressSkSet: boolean;
  ingressSkMasked: string;
  // Semantic Scholar API key（文献检索提额，可选）。
  s2KeySet: boolean;
  s2KeyMasked: string;
}
