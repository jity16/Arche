import { Loader2, Save, Settings2, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { archeApi, type ConfigPatch } from "../api";
import type { ArcheConfig } from "../types";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-xs font-medium text-slate-700">{label}</span>
        {hint && <span className="text-[10px] text-slate-400">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-800 outline-none transition focus:border-[#14532d] focus:ring-4 focus:ring-[#14532d]/10 disabled:bg-slate-50 disabled:opacity-60";
const defaultExpertBaseUrl =
  "https://h.pjlab.org.cn/kapi/workspace.kubebrain.io/ailab-ai4chem/lyq-test-k62j9-13402-worker-0.liyuqiang/18081/v1";
const defaultGaussianBaseUrl =
  "https://h.pjlab.org.cn/kapi/workspace.kubebrain.io/ailab-ai4chem/lyq-test-r8488-25714-worker-0.liyuqiang/vscode/proxy/18081";

export function ConfigModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: (cfg: ArcheConfig) => void;
}) {
  const [cfg, setCfg] = useState<ArcheConfig | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [expertBaseUrl, setExpertBaseUrl] = useState("");
  const [gaussianBaseUrl, setGaussianBaseUrl] = useState("");
  const [apiKeyHeader, setApiKeyHeader] = useState("");
  const [expertReview, setExpertReview] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [ingressAk, setIngressAk] = useState("");
  const [ingressSk, setIngressSk] = useState("");
  const [s2Key, setS2Key] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 仅在「打开」时拉取一次配置并初始化表单。依赖只放 [open]：
  // 绝不能把 onClose 放进来 —— 父组件每次重渲染都会传入新的 onClose 函数引用，
  // 若作为依赖会让本 effect 重跑、把用户正在填写的表单字段重置清空（历史 bug 现场）。
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    setApiKey("");
    setIngressAk("");
    setIngressSk("");
    setS2Key("");
    archeApi
      .getConfig()
      .then((c) => {
        setCfg(c);
        setBaseUrl(c.baseUrl);
        setModel(c.model);
        setExpertBaseUrl(c.expertBaseUrl);
        setGaussianBaseUrl(c.gaussianBaseUrl);
        setApiKeyHeader(c.apiKeyHeader);
        setExpertReview(c.expertReview);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [open]);

  // Escape 关闭 + 锁 body 滚动。锁 body：否则 body 与弹窗两条滚动条并存、弹窗高度变化时抖动 = 闪烁
  // （html 已 scrollbar-gutter:stable 预留槽，锁 body 不引起布局跳）。重订阅幂等、不碰表单字段。
  useEffect(() => {
    if (!open) return;
    // 锁页面滚动:真正的滚动容器是 html（html{overflow-y:scroll}），锁 documentElement 而非 body，
    // 否则滚动链传到背后页面、半透明遮罩后内容移动 = 滚动时遮罩闪。html 已预留滚动条槽，锁住不跳。
    const html = document.documentElement;
    const prevOverflow = html.style.overflow;
    html.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      html.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open) return null;
  const locked = cfg ? !cfg.enabled : false;

  const save = () => {
    setSaving(true);
    setError(null);
    const patch: ConfigPatch = { baseUrl, model, expertBaseUrl, gaussianBaseUrl, apiKeyHeader, expertReview };
    if (apiKey.trim()) patch.apiKey = apiKey.trim();
    if (ingressAk.trim()) patch.ingressAk = ingressAk.trim();
    if (ingressSk.trim()) patch.ingressSk = ingressSk.trim();
    if (s2Key.trim()) patch.s2Key = s2Key.trim();
    archeApi
      .updateConfig(patch)
      .then((c) => {
        setCfg(c);
        setApiKey("");
        setIngressAk("");
        setIngressSk("");
        onSaved?.(c);
        onClose();
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setSaving(false));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-hidden bg-slate-900/40 p-4 sm:p-10"
      onClick={onClose}
    >
      <div
        className="console-scroll relative my-auto max-h-full w-full max-w-lg transform-gpu overflow-y-auto overscroll-contain rounded-lg border border-slate-200 bg-white shadow-[0_28px_80px_rgba(15,23,42,0.28)] [contain:paint]"
        role="dialog"
        aria-modal="true"
        aria-label="模型服务配置"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 bg-[#fbfcfb] px-5 py-3.5">
          <div className="flex items-center gap-2">
            <Settings2 className="size-4 text-[#14532d]" />
            <h2 className="text-sm font-semibold text-slate-900">模型服务配置</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭" className="rounded-md p-1 text-slate-400 hover:bg-slate-50 hover:text-slate-600">
            <X className="size-4" />
          </button>
        </div>

        {loading ? (
          <div className="px-5 py-12 text-center text-slate-400">
            <Loader2 className="mx-auto size-5 animate-spin" />
          </div>
        ) : (
          <div className="space-y-4 px-5 py-4">
            {locked && (
              <div className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 ring-1 ring-inset ring-amber-500/20">
                部署已锁定配置（ARCHE_UI_CONFIG_ENABLED=0），此处仅供查看。
              </div>
            )}
            <Field label="服务地址" hint="OpenAI 兼容端点 /v1">
              <input className={inputCls} disabled={locked} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://100.98.54.47:18081/v1" />
            </Field>
            <Field label="模型">
              <input className={inputCls} disabled={locked} value={model} onChange={(e) => setModel(e.target.value)} placeholder="interns2-preview-sft" />
            </Field>
            <Field label="专家模型地址" hint="ARCHE-Chem OpenAI 兼容端点 /v1">
              <input
                className={inputCls}
                disabled={locked}
                value={expertBaseUrl}
                onChange={(e) => setExpertBaseUrl(e.target.value)}
                placeholder={defaultExpertBaseUrl}
              />
            </Field>
            <Field label="Gaussian 服务地址" hint="基础地址；运行时会自动拼接 /v1/gaussian/run">
              <input
                className={inputCls}
                disabled={locked}
                value={gaussianBaseUrl}
                onChange={(e) => setGaussianBaseUrl(e.target.value)}
                placeholder={defaultGaussianBaseUrl}
              />
            </Field>
            <Field label="鉴权头名" hint="自定义头网关填（如 x-api-key）；留空走标准 Bearer">
              <input className={inputCls} disabled={locked} value={apiKeyHeader} onChange={(e) => setApiKeyHeader(e.target.value)} placeholder="x-api-key" />
            </Field>
            <Field label="API 密钥" hint={cfg?.apiKeySet ? `已设置 ${cfg.apiKeyMasked}，留空则不变` : "尚未设置"}>
              <input
                className={inputCls}
                type="password"
                disabled={locked}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={cfg?.apiKeySet ? "••••（保持不变）" : "输入 API 密钥"}
              />
            </Field>

            <div className="border-t border-slate-200 pt-3.5">
              <p className="mb-3 text-[10px] font-medium uppercase tracking-wide text-slate-400">运行选项</p>
              <div className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2.5">
                <div className="min-w-0">
                  <div className="text-xs font-medium text-slate-700">专家复核</div>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-slate-400">
                    关掉后 planner/execution 不再逐步做 ARCHE-Chem 专家复核，整轮显著提速、并避开本地拉大模型。
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={expertReview}
                  aria-label="专家复核开关"
                  disabled={locked}
                  onClick={() => setExpertReview((v) => !v)}
                  className={`relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    expertReview ? "bg-[#14532d]" : "bg-slate-300"
                  }`}
                >
                  <span
                    className={`inline-block size-4 transform rounded-full bg-white shadow transition ${
                      expertReview ? "translate-x-4" : "translate-x-0.5"
                    }`}
                  />
                </button>
              </div>
            </div>

            <div className="border-t border-slate-200 pt-3.5">
              <p className="mb-3 text-[10px] font-medium uppercase tracking-wide text-slate-400">
                ingress 网关鉴权（可选 · Basic Auth）
              </p>
              <div className="space-y-4">
                <Field label="网关 Access Key" hint="等价 curl -u 的 AK；与 SK 同时填才生效，留空走标准 Bearer">
                  <input
                    className={inputCls}
                    type="password"
                    disabled={locked}
                    value={ingressAk}
                    onChange={(e) => setIngressAk(e.target.value)}
                    placeholder={cfg?.ingressAkSet ? `••••（已设置 ${cfg.ingressAkMasked}，留空不变）` : "（可选）输入网关 AK"}
                  />
                </Field>
                <Field label="网关 Secret Key" hint={cfg?.ingressSkSet ? `已设置 ${cfg.ingressSkMasked}，留空则不变` : "（可选）尚未设置"}>
                  <input
                    className={inputCls}
                    type="password"
                    disabled={locked}
                    value={ingressSk}
                    onChange={(e) => setIngressSk(e.target.value)}
                    placeholder={cfg?.ingressSkSet ? "••••（保持不变）" : "（可选）输入网关 SK"}
                  />
                </Field>
              </div>
            </div>

            <div className="border-t border-slate-200 pt-3.5">
              <p className="mb-3 text-[10px] font-medium uppercase tracking-wide text-slate-400">
                文献检索（可选 · Semantic Scholar）
              </p>
              <Field
                label="Semantic Scholar API Key"
                hint={cfg?.s2KeySet ? `已设置 ${cfg.s2KeyMasked}，留空则不变` : "（可选）匿名池会限流；填免费 key 提额，留空仍可用"}
              >
                <input
                  className={inputCls}
                  type="password"
                  disabled={locked}
                  value={s2Key}
                  onChange={(e) => setS2Key(e.target.value)}
                  placeholder={cfg?.s2KeySet ? "••••（保持不变）" : "（可选）输入 Semantic Scholar API key"}
                />
              </Field>
            </div>

            {error && <p className="text-xs text-rose-600">{error}</p>}
          </div>
        )}

        <div className="flex justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-slate-600 transition hover:bg-white">
            取消
          </button>
          <button
            type="button"
            onClick={save}
            disabled={locked || saving || loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#14532d] px-4 py-1.5 text-sm font-semibold text-white transition hover:bg-[#166534] disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />} 保存
          </button>
        </div>
      </div>
    </div>
  );
}
