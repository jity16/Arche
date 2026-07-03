import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("active research session uses a main workflow workbench with a file preview workspace", async () => {
  const app = await readFile(join(here, "../App.tsx"), "utf8");
  const detail = await readFile(join(here, "DetailView.tsx"), "utf8");
  const loop = await readFile(join(here, "AgentLoop.tsx"), "utf8");
  const resultPanel = await readFile(join(here, "ResultPanel.tsx"), "utf8");
  const parse = await readFile(join(here, "../lib/parse.ts"), "utf8");
  const types = await readFile(join(here, "../types.ts"), "utf8");

  assert.match(app, /max-w-\[1480px\] flex-col/, "the active research surface should stay wide but bounded for readable reports");
  assert.match(detail, /w-full max-w-none/, "detail sessions should not be constrained to a narrow article width");
  assert.match(detail, /<AgentLoop state=\{loop\} running=\{running\} result=\{result\}/, "the live workbench should receive persisted timeline and artifacts");
  assert.match(types, /type: "snapshot"/, "the stream contract should include running snapshots with intermediate files");
  assert.match(app, /e\.type === "snapshot"/, "active sessions should merge running snapshots instead of waiting for final done");
  assert.match(app, /artifacts: e\.artifacts/, "snapshot handling should pass intermediate artifacts into the workbench");
  assert.match(detail, /showTimeline=\{false\}/, "the final report should not duplicate the main workflow cards");
  assert.match(detail, /showArtifacts=\{false\}/, "files should live in the right-side workspace, not as a second report list");
  assert.doesNotMatch(detail, /ARCHE Research/, "the large standalone research-process header should be removed from the main session");
  assert.doesNotMatch(detail, /<article className="flex gap-3">/, "the process area should use the full workbench width instead of chat bubbles");

  assert.match(loop, /WorkflowNodeCard/, "live progress should render as separate workflow node cards");
  assert.match(loop, /WorkflowWorkspace/, "the live process should reserve a right-side workspace");
  assert.match(loop, /PreviewDialog/, "file previews should open in a dialog instead of occupying the process card");
  assert.match(loop, /ArtifactPreview/, "the dialog should reuse the readable artifact preview renderer");
  assert.match(loop, /collapseTimeline/, "completed and historical runs should drive the workbench from persisted timeline data");
  assert.match(loop, /xl:grid-cols-\[minmax\(0,1fr\)_360px\]/, "desktop workflow layout should reserve a right-side file workspace");
  assert.match(loop, /setPreviewFile/, "clicking a file should open a preview modal");
  assert.match(loop, /role="dialog"/, "the preview modal should expose dialog semantics");
  assert.match(loop, /工作区/, "the right-side workspace should be clearly labeled");
  assert.match(loop, /workspaceCollapsed/, "the right-side workspace control should manage collapsed state");
  assert.match(loop, /setWorkspaceCollapsed/, "the workspace header icon must perform a real action");
  assert.match(loop, /aria-label=\{workspaceCollapsed \? "展开工作区" : "收起工作区"\}/, "the workspace toggle should be named as a real control");
  assert.match(loop, /onClick=\{\(\) => setWorkspaceCollapsed/, "the workspace toggle should be clickable");
  assert.match(loop, /xl:grid-cols-\[minmax\(0,1fr\)_48px\]/, "collapsed workspace should release most of the right column width");
  assert.doesNotMatch(loop, /<PanelRightOpen className="size-4 shrink-0 text-slate-300" \/>/, "the workspace header must not render a fake standalone icon button");
  assert.match(loop, /整体进度/, "the workbench should show a compact overall workflow status below the question");
  assert.match(loop, /SegmentedPhaseProgress/, "overall progress should be rendered as five stage segments");
  assert.match(loop, /phaseProgressSegments/, "the five progress segments should be derived from workflow phase state");
  assert.match(loop, /FLOW_PHASES\.map/, "the progress bar should map directly over the five canonical phases");
  assert.doesNotMatch(loop, /mt-3 flex flex-wrap gap-1\.5[\s\S]*FLOW_PHASES\.map/, "phase pills below the segmented progress bar duplicate the same state and should not render");
  assert.match(loop, /arche-flow-scan/, "running progress segments should have a flowing scan animation");
  assert.match(loop, /statusNarrative/, "node card copy should change with each node status instead of staying static");
  assert.match(loop, /node\.status === "running"/, "running cards should show live status language");
  assert.match(loop, /node\.status === "done"/, "completed cards should use completed copy");
  assert.match(loop, /node\.status === "waiting"/, "waiting cards should use waiting copy");
  assert.match(loop, /node\.status === "failed"/, "failed cards should use failed copy");
  assert.match(loop, /arche-node-flow/, "running cards should carry a subtle flow treatment");
  assert.doesNotMatch(loop, /style=\{\{ width: `\$\{Math\.max\(progress/, "overall progress should not be a single continuously filled bar");
  assert.doesNotMatch(loop, /function Node/, "the old icon-node flow should not be used for live process display");
  assert.doesNotMatch(loop, /const Connector/, "the old connector line should be removed from the live process display");

  assert.match(resultPanel, /showTimeline = true/, "timeline rendering should remain available when ResultPanel is used standalone");
  assert.match(resultPanel, /showArtifacts = true/, "artifact rendering should remain available when ResultPanel is used standalone");
  assert.doesNotMatch(resultPanel, /max-w-\[1120px\]/, "final reports should align with the surrounding workflow workbench");
  assert.doesNotMatch(parse, /工作流进行中 · 运行尚未结束/, "running history should not render the rejected oversized status phrase");
});
