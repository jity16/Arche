import { CheckCircle2, CircleSlash, Loader2, MessageSquareText, Plus, Search, Trash2, XCircle } from "lucide-react";
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
  onNewSession,
}: {
  items: RunListItem[];
  loading: boolean;
  error: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRefresh: () => void;
  onNewSession: () => void;
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const confirmItem = items.find((i) => i.id === confirmId) ?? null;
  const filtered = query.trim()
    ? items.filter((it) => plainQuestion(it.question).toLowerCase().includes(query.trim().toLowerCase()))
    : items;
  return (
    <>
      <aside className="flex h-full min-h-0 flex-col overflow-hidden rounded-none border border-l-0 border-slate-200 bg-[#fbfcfb] shadow-[14px_0_40px_rgba(15,23,42,0.045)]">
        <div className="border-b border-slate-200 px-3 py-3">
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <MessageSquareText className="size-4 text-[#14532d]" />
              <h2 className="text-sm font-semibold text-slate-900">Researchs</h2>
              <span className="rounded-md bg-white px-1.5 py-0.5 text-xs text-slate-500 ring-1 ring-inset ring-slate-200">{items.length}</span>
            </div>
            <button
              type="button"
              onClick={onNewSession}
              title="新增研究 session"
              aria-label="新增研究 session"
              className="flex size-8 items-center justify-center rounded-md bg-[#14532d] text-white shadow-[0_10px_22px_rgba(20,83,45,0.18)] transition hover:bg-[#166534] focus:outline-none focus:ring-4 focus:ring-[#14532d]/15"
            >
              <Plus className="size-4" />
            </button>
          </div>
        </div>

        {items.length > 0 && (
          <div className="border-b border-slate-200 px-3 py-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-2.5 size-3.5 text-slate-400" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索研究 session"
                className="h-9 w-full rounded-md border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-[#14532d] focus:bg-white focus:ring-4 focus:ring-[#14532d]/10"
              />
            </div>
          </div>
        )}

        <div className="console-scroll min-h-0 flex-1 overflow-y-auto p-2">
          {loading && items.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-slate-400">加载中…</div>
          ) : error && items.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-slate-400">
              加载失败 ·{" "}
              <button type="button" onClick={onRefresh} className="text-[#14532d] hover:underline">
                重试
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-slate-400">暂无研究 session</div>
          ) : filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-slate-400">无匹配研究 session</div>
          ) : (
            <ul className="space-y-1">
              {filtered.map((it) => {
                const state =
                  it.status === "running"
                    ? "running"
                    : it.status === "cancelled" || it.status === "canceled"
                      ? "cancelled"
                    : it.status === "interrupted" || it.status === "timeout"
                      ? "interrupted"
                      : it.exitCode === 0
                        ? "ok"
                        : "failed";
                const active = it.id === activeId;
                const stripe =
                  state === "ok"
                    ? "bg-emerald-500"
                    : state === "running"
                      ? "bg-amber-400"
                      : state === "cancelled"
                        ? "bg-slate-300"
                      : state === "interrupted"
                        ? "bg-slate-300"
                        : "bg-rose-500";
                return (
                  <li key={it.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelect(it.id)}
                    className={`relative flex w-full items-start gap-2 overflow-hidden rounded-md border py-2.5 pl-3 pr-8 text-left transition ${
                      active ? "border-[#b7d4c0] bg-white shadow-sm" : "border-transparent hover:border-slate-200 hover:bg-white"
                    }`}
                  >
                    <span className={`absolute inset-y-2 left-0 w-1 rounded-r ${stripe}`} />
                    {state === "running" ? (
                      <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-amber-500" />
                    ) : state === "ok" ? (
                      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />
                    ) : state === "cancelled" ? (
                      <CircleSlash className="mt-0.5 size-4 shrink-0 text-slate-400" />
                    ) : state === "interrupted" ? (
                      <CircleSlash className="mt-0.5 size-4 shrink-0 text-slate-400" />
                    ) : (
                      <XCircle className="mt-0.5 size-4 shrink-0 text-rose-500" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-800">
                        {plainQuestion(it.question) || "（空问题）"}
                      </div>
                      <div className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-400">
                        <span className="font-mono">{fmtTime(it.createdAt)}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 ${
                            state === "ok"
                              ? "bg-emerald-50 text-emerald-700"
                              : state === "running"
                                ? "bg-amber-100 text-amber-600"
                                : state === "cancelled"
                                  ? "bg-slate-100 text-slate-500"
                                : state === "interrupted"
                                  ? "bg-slate-100 text-slate-500"
                                  : "bg-rose-100 text-rose-600"
                          }`}
                        >
                          {state === "ok"
                            ? "成功"
                            : state === "running"
                              ? "运行中"
                              : state === "cancelled"
                                ? "已取消"
                                : state === "interrupted"
                                  ? "中断"
                                  : "失败"}
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
                    title="删除研究 session"
                    aria-label="删除研究 session"
                    className="absolute right-1.5 top-2 rounded p-1 text-slate-300 opacity-0 transition hover:bg-rose-50 hover:text-rose-500 focus:opacity-100 group-hover:opacity-100"
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
            <AlertDialogTitle>删除这个研究 session？</AlertDialogTitle>
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
