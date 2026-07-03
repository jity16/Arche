import type { RunListItem, RunRecord } from "../types";

export function syncHistoryItemFromRun(items: RunListItem[], rec: RunRecord): RunListItem[] {
  let changed = false;
  const next = items.map((item) => {
    if (item.id !== rec.id) return item;

    const synced = {
      ...item,
      createdAt: rec.createdAt,
      question: rec.question,
      exitCode: typeof rec.exitCode === "number" ? rec.exitCode : item.exitCode,
      status: rec.status ?? item.status,
    };

    changed =
      changed ||
      synced.createdAt !== item.createdAt ||
      synced.question !== item.question ||
      synced.exitCode !== item.exitCode ||
      synced.status !== item.status;

    return synced;
  });

  return changed ? next : items;
}
