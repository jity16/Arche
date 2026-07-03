import type { AgentInfo, ArcheConfig, HealthInfo, ModelStatus, RunListItem, RunRecord, RunResult, StreamEvent } from "./types";

export interface ConfigPatch {
  baseUrl?: string;
  model?: string;
  apiKeyHeader?: string;
  /** 专家复核开关（planner/execution 逐步 ARCHE-Chem 复核）；关掉大幅提速。 */
  expertReview?: boolean;
  apiKey?: string;
  clearApiKey?: boolean;
  // ingress 网关 Basic Auth 凭证（AK/SK）。仅显式传入非空才更新；clear* 显式清除。
  ingressAk?: string;
  ingressSk?: string;
  clearIngressAk?: boolean;
  clearIngressSk?: boolean;
  // Semantic Scholar API key（文献检索提额，可选）。仅显式传入非空才更新；clear 显式清除。
  s2Key?: string;
  clearS2Key?: boolean;
}

// 路径路由(方案3)下整个 SPA 挂在 /apps/<id>/，运行时从 URL 推导前缀，
// 使所有同源 API 调用都带上它（根路径部署时为空，行为不变）。
const BASE_PATH = (window.location.pathname.match(/^(\/apps\/[^/]+)/) || [])[1] || "";

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE_PATH + url, init);
  const text = await res.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`非 JSON 响应 (${res.status}): ${text.slice(0, 200)}`);
  }
  if (!res.ok) {
    const msg = (data as { error?: string })?.error ?? `请求失败 (${res.status})`;
    // 4xx/5xx 仍可能带 RunResult 体（如 exitCode!=0），交由调用方按需处理。
    const err = new Error(msg) as Error & { payload?: unknown; status?: number };
    err.payload = data;
    err.status = res.status;
    throw err;
  }
  return data as T;
}

export const archeApi = {
  health: () => getJson<HealthInfo>("/healthz"),
  info: () => getJson<AgentInfo>("/api/info"),
  // 真实多智能体工作流；模型服务地址/模型/Key 由服务端环境变量或 /api/config 决定。
  run: (question: string) =>
    getJson<RunResult>("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  listRuns: (limit = 50) => getJson<{ items: RunListItem[]; total: number }>(`/api/runs?limit=${limit}`),
  getRun: (id: string) => getJson<RunRecord>(`/api/runs/${encodeURIComponent(id)}`),
  // 流式运行：逐行读取 NDJSON，把 controller 真实阶段/轮次事件回调给调用方。
  runStream: async (question: string, onEvent: (e: StreamEvent) => void, signal?: AbortSignal): Promise<void> => {
    const res = await fetch(BASE_PATH + "/api/run/stream", {
      method: "POST",
      // Accept: text/event-stream 是关键 —— a3s-gateway 仅凭请求的这个头判定为流式响应并逐块透传；
      // 缺它则网关会缓冲整段响应,前端在运行期间收不到任何 NDJSON 事件(界面卡在"等待进入循环"、左侧无记录)。
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ question }),
      signal,
    });
    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => "");
      throw new Error(`stream failed (${res.status}) ${text.slice(0, 200)}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        try {
          onEvent(JSON.parse(t) as StreamEvent);
        } catch {
          /* 跳过不完整/非 JSON 行 */
        }
      }
    }
    const tail = buf.trim();
    if (tail) {
      try {
        onEvent(JSON.parse(tail) as StreamEvent);
      } catch {
        /* ignore */
      }
    }
  },
  deleteRun: async (id: string): Promise<void> => {
    const res = await fetch(`${BASE_PATH}/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`delete failed (${res.status})`);
  },
  cancelRun: async (id: string): Promise<void> => {
    const res = await fetch(`${BASE_PATH}/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
    if (!res.ok) throw new Error(`cancel failed (${res.status})`);
  },
  // 产物下载 URL：直接挂在 <a href download> 上，浏览器走 send_from_directory 端点取文件。
  // （RunRecord.artifacts 已内嵌 name+size，无需单独 list 端点；后端 list 端点仍保留备用。）
  artifactUrl: (id: string, name: string) =>
    `${BASE_PATH}/api/runs/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(name)}`,
  modelStatus: () => getJson<ModelStatus>("/api/model-status"),
  getConfig: () => getJson<ArcheConfig>("/api/config"),
  updateConfig: (patch: ConfigPatch) =>
    getJson<ArcheConfig>("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
};
