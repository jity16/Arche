import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("research workspace keeps sessions in a left rail and runs inside the active conversation", async () => {
  const app = await readFile(join(here, "../App.tsx"), "utf8");
  const historyPanel = await readFile(join(here, "HistoryPanel.tsx"), "utf8");
  const header = await readFile(join(here, "Header.tsx"), "utf8");
  const css = await readFile(join(here, "../index.css"), "utf8");

  assert.match(app, /startNewSession/, "the shell should provide an explicit new-session action");
  assert.match(app, /onNewSession=\{startNewSession\}/, "the session rail should own the new-session entry point");
  assert.doesNotMatch(app, /view === "detail"/, "running a research task should not navigate away from the workspace");
  assert.doesNotMatch(app, /setView\("detail"\)/, "submitting a question should append to the active session instead of page-switching");
  assert.match(app, /<div className="flex h-full overflow-hidden flex-col">/, "the app shell should not let the document scroll under the breadcrumb/header area");
  assert.match(app, /<main className="min-h-0 w-full flex-1 overflow-hidden">/, "workspace scrolling should be contained inside the workbench, not the page");
  assert.match(app, /lg:grid-cols-\[304px_minmax\(0,1fr\)\]/, "the left rail should occupy the first fixed grid column");
  assert.doesNotMatch(app, /<main className="mx-auto w-full max-w-\[1480px\]/, "the left rail should not sit inside a centered max-width container");
  assert.match(app, /mx-auto flex min-h-full w-full max-w-\[1480px\] flex-col/, "the central research surface should use wide screens without creating unreadable line lengths");
  assert.match(app, /<section className="console-scroll min-h-0 overflow-y-auto/, "only the research content pane should scroll vertically");
  assert.doesNotMatch(header, /sticky top-0/, "the header should occupy layout space instead of covering scrolled content");
  assert.doesNotMatch(historyPanel, /\blg:sticky\b/, "the flush rail should be part of the fixed-height workbench, not another sticky layer");
  assert.match(css, /html,\s*body\s*\{[\s\S]*overflow:\s*hidden;/, "the document itself should not scroll under an external breadcrumb bar");

  assert.doesNotMatch(app, /label="Sessions"/, "the conversation title should not show workspace counter chips");
  assert.doesNotMatch(app, /label="Researchs"/, "the conversation title should not show workspace counter chips");
  assert.doesNotMatch(app, /<WorkspaceChip\b/, "the conversation title should not render the status chip row");
  assert.doesNotMatch(app, /ARCHE RESEARCH/, "the standalone conversation heading strip should be removed");
  assert.doesNotMatch(app, /当前研究会话|新建研究会话/, "the standalone conversation heading strip should be removed");
  assert.match(historyPanel, /Researchs/, "the left rail should use Researchs as the collection name");
  assert.match(historyPanel, /placeholder="搜索研究 session"/, "search should describe research sessions precisely");
  assert.match(historyPanel, /title="新增研究 session"/, "the rail header should expose a compact new-session icon button");
  assert.doesNotMatch(historyPanel, /RotateCw/, "the old header refresh icon should be replaced by the new-session action");
  assert.doesNotMatch(historyPanel, />\s*新会话\s*</, "new session should not be a large labeled CTA button");
  assert.match(historyPanel, /rounded-none/, "the left rail should read as a flush sidebar, not a floating card");
  assert.match(historyPanel, /border-l-0/, "the left rail should not draw a gutter on the viewport edge");
  assert.doesNotMatch(historyPanel, /rounded-lg/, "the flush left rail should not keep card-style rounded corners");
});
