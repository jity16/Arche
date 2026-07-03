import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { archeApi } from "./api";
import { applyEvent, emptyLoop, type LoopState } from "./components/AgentLoop";
import { ChemDecor } from "./components/ChemDecor";
import { ConfigModal } from "./components/ConfigModal";
import { DetailView } from "./components/DetailView";
import { Header } from "./components/Header";
import { HistoryPanel } from "./components/HistoryPanel";
import { QuestionForm } from "./components/QuestionForm";
import { syncHistoryItemFromRun } from "./lib/historyState";
import type { AgentInfo, HealthInfo, ModelStatus, RunListItem, RunResult } from "./types";

function ModelBanner({ status, onConfigure }: { status: ModelStatus | null; onConfigure: () => void }) {
  if (!status) return null;
  const m = status.model;
  if (m.configured && m.reachable !== false) return null;
  const unreachable = m.configured && m.reachable === false;
  return (
    <div
      className={`mb-5 flex items-center gap-3 rounded-lg border px-4 py-3 text-sm ${
        unreachable ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50 text-amber-800"
      }`}
    >
      <AlertTriangle className="size-4 shrink-0" />
      <span className="flex-1">
        {unreachable
          ? `模型端点不可达${m.baseUrl ? `（${m.baseUrl}）` : ""}，运行会失败 —— 请检查网络 / Tailnet，或在配置里更换地址。`
          : "尚未配置模型服务，无法进行真实推理。"}
      </span>
      <button
        type="button"
        onClick={onConfigure}
        className="shrink-0 rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-inset ring-slate-200 transition hover:bg-slate-50"
      >
        打开配置
      </button>
    </div>
  );
}

export default function App() {
  const [info, setInfo] = useState<AgentInfo | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);

  const [question, setQuestion] = useState("");
  const [running, setRunning] = useState(false);
  const [loop, setLoop] = useState<LoopState>(emptyLoop());
  const [result, setResult] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<RunListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const [detailLoading, setDetailLoading] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  // 「运行中」记录回看：轮询 getRun 直到终态的定时器句柄；selectedRef 是当前详情页锁定的 run id，
  // 供异步回调校验（用户中途切走则丢弃迟到结果，避免覆盖当前视图）。
  const pollRef = useRef<number | null>(null);
  const selectedRef = useRef<string | null>(null);
  const runSeqRef = useRef(0);
  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // 轮询去抖：health / modelStatus / history 每轮都会拿到新对象引用，无脑 setState 会让整页
  // (含正在填写的 ConfigModal) 周期性重渲染 → 表单被冲掉、UI 闪烁。这里缓存"有意义字段"的指纹，
  // 内容真变了才 setState。
  const healthKeyRef = useRef<string | null>(null);
  const modelKeyRef = useRef<string | null>(null);
  const historyKeyRef = useRef<string | null>(null);

  const fetchHistory = useCallback(() => {
    // 仅首次加载（还没拿到任何指纹）才显示骨架/加载态，后续刷新静默更新、不闪。
    if (historyKeyRef.current === null) setHistoryLoading(true);
    archeApi
      .listRuns(50)
      .then((r) => {
        const key = r.items.map((it) => `${it.id}:${it.status}:${it.exitCode ?? ""}`).join("|");
        if (key !== historyKeyRef.current) {
          historyKeyRef.current = key;
          setHistory(r.items);
        }
        setHistoryError(false);
      })
      .catch(() => setHistoryError(true))
      .finally(() => setHistoryLoading(false));
  }, []);

  // 配置保存后/手动触发：立刻刷新健康灯、模型可达 banner、agent 元信息。
  const refreshStatus = useCallback(() => {
    archeApi
      .modelStatus()
      .then((s) => {
        modelKeyRef.current = `${s?.model?.configured ?? ""}|${s?.model?.reachable ?? ""}|${s?.model?.baseUrl ?? ""}`;
        setModelStatus(s);
      })
      .catch(() => {
        modelKeyRef.current = null;
        setModelStatus(null);
      });
    archeApi
      .health()
      .then((h) => {
        healthKeyRef.current = JSON.stringify(h ?? null);
        setHealth(h);
        setHealthError(false);
      })
      .catch(() => setHealthError(true));
    archeApi
      .info()
      .then(setInfo)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    archeApi
      .info()
      .then(setInfo)
      .catch(() => undefined);
    fetchHistory();

    let alive = true;
    // 防抖计数：模型端点活体探测会因网络抖动间歇失败，单次失败就翻红会让横幅每轮闪现/消失。
    // 连续 2 次才认定异常；恢复正常立即清零。探测本身失败（网络抖动）保留上次状态、不清空。
    let unreachableHits = 0;
    let healthErrHits = 0;
    const ping = () => {
      archeApi
        .health()
        .then((h) => {
          if (!alive) return;
          healthErrHits = 0;
          const key = JSON.stringify(h ?? null);
          if (key !== healthKeyRef.current) {
            healthKeyRef.current = key;
            setHealth(h);
          }
          setHealthError(false);
        })
        .catch(() => {
          if (!alive) return;
          healthErrHits += 1;
          if (healthErrHits >= 2) setHealthError(true);
        });
      archeApi
        .modelStatus()
        .then((s) => {
          if (!alive) return;
          // 只取会影响展示的字段做指纹，避免响应里的时间戳/探测耗时之类的易变字段每轮触发重渲染。
          const key = `${s?.model?.configured ?? ""}|${s?.model?.reachable ?? ""}|${s?.model?.baseUrl ?? ""}`;
          if (s?.model?.reachable === false) {
            unreachableHits += 1;
            // 连续 2 次不可达才显示横幅，且内容真变才 setState
            if (unreachableHits >= 2 && key !== modelKeyRef.current) {
              modelKeyRef.current = key;
              setModelStatus(s);
            }
          } else {
            unreachableHits = 0;
            if (key !== modelKeyRef.current) {
              modelKeyRef.current = key;
              setModelStatus(s); // 可达/已配置立即恢复
            }
          }
        })
        .catch(() => {
          /* 探测调用本身失败（网络抖动）：保留上次状态，不清空、不闪烁 */
        });
    };
    ping();
    const t = setInterval(ping, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [fetchHistory]);

  const onRun = useCallback(async () => {
    const q = question.trim();
    if (!q || running) return;
    stopPoll(); // 开新 run：停掉任何历史回看的轮询
    selectedRef.current = null;
    activeRunIdRef.current = null;
    const seq = runSeqRef.current + 1;
    runSeqRef.current = seq;
    const isCurrentRun = () => runSeqRef.current === seq;
    const ac = new AbortController();
    abortRef.current = ac;
    setRunning(true);
    setError(null);
    setResult(null);
    setSelectedRunId(null);
    setLoop(emptyLoop());
    setDetailLoading(false);

    let done: RunResult | null = null;
    let runId: string | null = null;
    let aborted = false;
    try {
      await archeApi.runStream(q, (e) => {
        if (!isCurrentRun()) return;
        setLoop((prev) => applyEvent(prev, e)); // 实时同步后端阶段/轮次
        if (e.type === "start") {
          runId = e.id;
          activeRunIdRef.current = e.id;
          selectedRef.current = e.id;
          setSelectedRunId(e.id);
          fetchHistory(); // 立刻在历史里显示"运行中"
        }
        if (e.type === "done") {
          done = {
            id: e.id,
            createdAt: e.createdAt,
            exitCode: e.exitCode,
            status: e.status,
            result: e.result ?? null,
            timeline: e.timeline,
            artifacts: e.artifacts,
            stdout: e.stdout,
            stderr: e.stderr,
          };
        }
      }, ac.signal);
      if (!isCurrentRun()) return;
      if (done) {
        setResult(done);
        setSelectedRunId((done as RunResult).id ?? null);
      } else if (runId) {
        // 流结束但没收到 done：轮询后端记录（可能已写终态），不直接红屏。
        try {
          const rec = await archeApi.getRun(runId);
          if (typeof rec.exitCode === "number") {
            setResult(rec);
            setSelectedRunId(rec.id);
          } else {
            aborted = true; // 不弹结果弹窗、不报红
            setLoop((prev) => ({ ...prev, active: "连接中断（运行已记入历史，可回看）", finished: true }));
          }
        } catch {
          aborted = true;
          setLoop((prev) => ({ ...prev, active: "连接中断", finished: true }));
        }
      } else {
        setError("运行失败：未收到任何事件");
      }
    } catch (err) {
      if (!isCurrentRun()) return;
      if ((err as Error).name === "AbortError") {
        aborted = true;
        setLoop((prev) => ({ ...prev, active: "已取消", finished: true }));
      } else {
        setError((err as Error).message || "运行失败");
      }
    } finally {
      if (isCurrentRun()) {
        setRunning(false);
        activeRunIdRef.current = null;
        abortRef.current = null;
        fetchHistory(); // 结果已在会话流内联展示，无需弹窗
      }
    }
  }, [question, running, fetchHistory, stopPoll]);

  const onStop = useCallback(() => {
    const runId = activeRunIdRef.current;
    if (!runId) {
      abortRef.current?.abort();
      return;
    }
    void archeApi
      .cancelRun(runId)
      .then(() => {
        abortRef.current?.abort();
      })
      .catch((err) => {
        setError(`停止失败：${(err as Error).message || "无法取消当前运行"}`);
      })
      .finally(() => fetchHistory());
  }, [fetchHistory]);

  const startNewSession = useCallback(() => {
    stopPoll();
    runSeqRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    activeRunIdRef.current = null;
    selectedRef.current = null;
    setRunning(false);
    setQuestion("");
    setLoop(emptyLoop());
    setResult(null);
    setError(null);
    setSelectedRunId(null);
    setDetailLoading(false);
  }, [stopPoll]);

  const onSelectHistory = useCallback(
    (id: string) => {
      stopPoll();
      runSeqRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      activeRunIdRef.current = null;
      setRunning(false);
      setResult(null);
      setError(null);
      setLoop(emptyLoop()); // 查看历史时不残留上一次的实时管线图
      setSelectedRunId(id);
      selectedRef.current = id;
      setDetailLoading(true);

      // 载入记录；若仍是 running（断连≠取消，后端仍在跑），轮询到终态自动收敛为最终结果。
      const load = (first: boolean) => {
        archeApi
          .getRun(id)
          .then((rec) => {
            if (selectedRef.current !== id) return; // 用户已切走：丢弃迟到结果，别覆盖当前视图
            setResult(rec);
            setHistory((prev) => syncHistoryItemFromRun(prev, rec));
            if (first) setQuestion(rec.question);
            setError(null);
            if (rec.status === "running") {
              pollRef.current = window.setTimeout(() => load(false), 4000);
            }
          })
          .catch((e) => {
            if (selectedRef.current !== id) return;
            setError((e as Error).message || "读取记录失败");
          })
          .finally(() => {
            if (first) setDetailLoading(false);
          });
      };
      load(true);
    },
    [stopPoll],
  );

  const onDeleteHistory = useCallback(
    (id: string) => {
      archeApi
        .deleteRun(id)
        .then(() => {
          setHistory((prev) => prev.filter((it) => it.id !== id));
          if (selectedRunId === id) {
            stopPoll(); // 删除的正是当前正在轮询回看的 run：停轮询
            startNewSession();
          }
        })
        .catch(() => undefined);
    },
    [selectedRunId, startNewSession, stopPoll],
  );

  // 稳定化弹窗回调：传给 ConfigModal 的 onClose 若每次渲染都是新函数，
  // 会让其内部 effect（如 ConfigModal 的 Escape 监听）反复重订阅；useCallback 固定身份。
  const openConfig = useCallback(() => setConfigOpen(true), []);
  const closeConfig = useCallback(() => setConfigOpen(false), []);
  // 卸载时清掉轮询定时器，避免组件销毁后仍有 setTimeout 回调触发 setState。
  useEffect(() => stopPoll, [stopPoll]);

  const hasActiveSession = running || detailLoading || result !== null || error !== null || selectedRunId !== null;

  return (
    <div className="flex h-full overflow-hidden flex-col">
      <ChemDecor />
      <Header info={info} health={health} healthError={healthError} onOpenConfig={openConfig} />

      <main className="min-h-0 w-full flex-1 overflow-hidden">
        <div className="grid h-full w-full grid-rows-[minmax(220px,40vh)_minmax(0,1fr)] lg:grid-cols-[304px_minmax(0,1fr)] lg:grid-rows-none">
          <HistoryPanel
            items={history}
            loading={historyLoading}
            error={historyError}
            activeId={selectedRunId}
            onSelect={onSelectHistory}
            onDelete={onDeleteHistory}
            onRefresh={fetchHistory}
            onNewSession={startNewSession}
          />

          <section className="console-scroll min-h-0 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
            <div className="mx-auto flex min-h-full w-full max-w-[1160px] flex-col">
              <ModelBanner status={modelStatus} onConfigure={openConfig} />

              {hasActiveSession ? (
                <DetailView
                  question={question}
                  loop={loop}
                  running={running}
                  loading={detailLoading}
                  result={result}
                  error={error}
                  onStop={onStop}
                />
              ) : (
                <div className="flex flex-1 items-center justify-center py-8">
                  <QuestionForm
                    question={question}
                    setQuestion={setQuestion}
                    running={running}
                    disabled={running || !question.trim()}
                    onRun={onRun}
                    onStop={onStop}
                  />
                </div>
              )}
            </div>
          </section>
        </div>
      </main>

      <footer className="border-t border-slate-200 bg-white py-3">
        <p className="mx-auto max-w-7xl px-4 text-center text-xs text-slate-400 sm:px-6 lg:px-8">
          书安OS 内置 ARCHE · 应用型计算化学智能体 · 检索 / 假设 / 规划 / 执行 / 反思
        </p>
      </footer>

      <ConfigModal open={configOpen} onClose={closeConfig} onSaved={refreshStatus} />
    </div>
  );
}
