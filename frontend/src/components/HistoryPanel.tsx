import { CheckCircle2, CircleSlash, History, Loader2, RotateCw, Search, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import type { RunListItem } from "../types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "./ui/alert-dialog";

function fmtTime(ms: number): string {
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

/** 去掉 $...$ / \ce{} 等标记，给列表展示一个可读的纯文本摘要。 */
function plainQuestion(q: string): string {
  return q
    .replace(/\$\$?([^$]*)\$\$?/g, "$1")
    .replace(/\\ce\{([^}]*)\}/g, "$1")
    .replace(/\\[a-zA-Z]+/g, "")
    .replace(/[{}^_]/g, "")
    .trim();
}

export function HistoryPanel({
  items,
  loading,
  error,
  activeId,
  onSelect,
  onDelete,
  onRefresh,
}: {
  items: RunListItem[];
  loading: boolean;
  error: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRefresh: () => void;
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const confirmItem = items.find((i) => i.id === confirmId) ?? null;
  const filtered = query.trim()
    ? items.filter((it) => plainQuestion(it.question).toLowerCase().includes(query.trim().toLowerCase()))
    : items;
  return (
    <>
    <aside className="rounded-2xl border border-slate-200 bg-white/95 shadow-sm shadow-slate-200/50 backdrop-blur lg:sticky lg:top-6">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <History className="size-4 text-teal-600" />
          <h2 className="text-sm font-semibold text-slate-800">执行记录</h2>
          <span className="rounded-full bg-slate-100 px-1.5 text-xs text-slate-500">{items.length}</span>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          title="刷新"
          className="rounded-md p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
        >
          <RotateCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {items.length > 0 && (
        <div className="border-b border-slate-100 px-3 py-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 size-3.5 text-slate-300" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索问题…"
              className="w-full rounded-md border border-slate-200 bg-slate-50/60 py-1.5 pl-8 pr-2 text-xs text-slate-700 outline-none focus:border-teal-400 focus:bg-white"
            />
          </div>
        </div>
      )}

      <div className="max-h-[70vh] overflow-y-auto p-2">
        {loading && items.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-slate-400">加载中…</div>
        ) : error && items.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-slate-400">
            加载失败 ·{" "}
            <button type="button" onClick={onRefresh} className="text-teal-600 hover:underline">
              重试
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-slate-400">暂无执行记录，运行一次工作流试试。</div>
        ) : filtered.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-slate-400">无匹配记录</div>
        ) : (
          <ul className="space-y-1">
            {filtered.map((it) => {
              const state =
                it.status === "running"
                  ? "running"
                  : it.status === "interrupted" || it.status === "timeout"
                    ? "interrupted"
                    : it.exitCode === 0
                      ? "ok"
                      : "failed";
              const active = it.id === activeId;
              return (
                <li key={it.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelect(it.id)}
                    className={`flex w-full items-start gap-2 rounded-lg border py-2 pl-2.5 pr-8 text-left transition ${
                      active ? "border-teal-300 bg-teal-50" : "border-transparent hover:border-slate-200 hover:bg-slate-50"
                    }`}
                  >
                    {state === "running" ? (
                      <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-amber-500" />
                    ) : state === "ok" ? (
                      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-teal-500" />
                    ) : state === "interrupted" ? (
                      <CircleSlash className="mt-0.5 size-4 shrink-0 text-slate-400" />
                    ) : (
                      <XCircle className="mt-0.5 size-4 shrink-0 text-rose-500" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs font-medium text-slate-700">
                        {plainQuestion(it.question) || "（空问题）"}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-slate-400">
                        <span className="font-mono">{fmtTime(it.createdAt)}</span>
                        <span
                          className={`rounded px-1 ${
                            state === "ok"
                              ? "bg-teal-100 text-teal-600"
                              : state === "running"
                                ? "bg-amber-100 text-amber-600"
                                : state === "interrupted"
                                  ? "bg-slate-100 text-slate-500"
                                  : "bg-rose-100 text-rose-600"
                          }`}
                        >
                          {state === "ok" ? "成功" : state === "running" ? "运行中" : state === "interrupted" ? "中断" : "失败"}
                        </span>
                      </div>
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmId(it.id);
                    }}
                    title="删除记录"
                    aria-label="删除记录"
                    className="absolute right-1.5 top-1.5 rounded p-1 text-slate-300 opacity-0 transition hover:bg-rose-50 hover:text-rose-500 focus:opacity-100 group-hover:opacity-100"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>

      <AlertDialog open={!!confirmId} onOpenChange={(open) => !open && setConfirmId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这条执行记录？</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmItem ? `「${plainQuestion(confirmItem.question) || "（空问题）"}」` : ""}
              <br />
              删除后不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-rose-600 hover:bg-rose-500 focus:ring-rose-500/20"
              onClick={() => {
                if (confirmId) onDelete(confirmId);
                setConfirmId(null);
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
