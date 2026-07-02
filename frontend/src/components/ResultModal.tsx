import { X } from "lucide-react";
import { useEffect } from "react";
import type { RunResult } from "../types";
import { ResultPanel } from "./ResultPanel";

export function ResultModal({
  open,
  onClose,
  result,
  error,
}: {
  open: boolean;
  onClose: () => void;
  result: RunResult | null;
  error: string | null;
}) {
  useEffect(() => {
    if (!open) return;
    // 锁页面滚动 —— 真正的滚动容器是 html(因 html{overflow-y:scroll})，所以锁 documentElement
    // 而非 body，否则滚动到弹窗底部时会链传到背后页面、半透明遮罩后内容移动 = 滚动时遮罩闪。
    // html 已 scrollbar-gutter:stable 预留滚动条槽，锁住不会引起布局左右跳。
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

  return (
    // 覆盖层不滚动(overflow-hidden);弹窗高度锁死 ≤ 视口、内部单条滚动 —— 从结构上根除
    // "弹窗高度不固定 → 覆盖层/页面两条滚动条来回抖动 = 闪烁"。
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-hidden bg-slate-900/40 p-4 sm:p-8"
      onClick={onClose}
    >
      <div
        className="relative my-auto w-full max-w-2xl"
        role="dialog"
        aria-modal="true"
        aria-label="执行结果"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="absolute -right-3 -top-3 z-10 rounded-full bg-white p-1.5 text-slate-500 shadow-md ring-1 ring-slate-200 transition hover:text-slate-800"
        >
          <X className="size-4" />
        </button>
        <ResultPanel result={result} error={error} />
      </div>
    </div>
  );
}
