import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("scientific conclusion report is split into readable sections and filters noisy settings", async () => {
  const resultPanel = await readFile(join(here, "ResultPanel.tsx"), "utf8");
  const detail = await readFile(join(here, "DetailView.tsx"), "utf8");
  const app = await readFile(join(here, "../App.tsx"), "utf8");

  assert.match(resultPanel, /computeConclusionView/, "the report should derive a clean view model instead of dumping final_conclusion fields");
  assert.match(resultPanel, /normalizeConclusionSummary/, "old persisted English-template summaries should be rewritten for display");
  assert.match(resultPanel, /计算结果/, "computed values should have a dedicated section");
  assert.match(resultPanel, /限制/, "workflow issues should be grouped as limitations");
  assert.match(resultPanel, /下一步/, "follow-up actions should be grouped separately");
  assert.match(resultPanel, /filterContextEntries/, "settings should be filtered before display");
  assert.match(resultPanel, /isMechanismConclusionUnsafe/, "mechanism reports must suppress stale generic HOMO\\/SCF evidence");
  assert.match(resultPanel, /safeMechanismSummary/, "unsafe mechanism summaries should be replaced with a conservative human-readable statement");
  assert.match(resultPanel, /methodologicalConclusion/, "unsafe mechanism summaries should still preserve a useful methodological conclusion");
  assert.match(resultPanel, /buildMechanismValidationPlan/, "mechanism reports should surface a validation plan instead of saying nothing");
  assert.match(resultPanel, /integratedAnalysisSections/, "new final conclusions should render the controller's integrated analysis sections");
  assert.match(resultPanel, /buildLegacyIntegratedAnalysis/, "old persisted runs should still get a synthesized multi-node analysis instead of a shallow fallback");
  assert.match(resultPanel, /cleanMechanismReportSections/, "persisted mechanism reports should be rewritten away from diagnostic failure logs");
  assert.match(resultPanel, /结论摘要/, "the conclusion should include a report-style summary section");
  assert.match(resultPanel, /机理图景/, "the conclusion should explain the mechanistic picture");
  assert.match(resultPanel, /证据解释/, "the conclusion should explain what the previous nodes contributed");
  assert.match(resultPanel, /判定标准/, "the conclusion should state how the mechanism will be accepted");
  assert.doesNotMatch(resultPanel, /执行证据与缺口/, "scientific conclusions should not show execution failure-log sections");
  assert.doesNotMatch(resultPanel, /工作流成功率/, "scientific conclusions should not lead with workflow success-rate diagnostics");
  assert.match(resultPanel, /safeResults/, "unsafe computed metrics should not be shown as mechanism evidence");
  assert.doesNotMatch(resultPanel, /key_findings \?\? \[\]\)\.map\(valText\)/, "key findings should not be dumped as uncurated bullets");
  assert.doesNotMatch(resultPanel, /ctxEntries = Object\.entries\(ctx\)\.filter/, "raw chemistry context should not be displayed unfiltered");

  assert.match(app, /max-w-\[1480px\]/, "the result workbench should keep a readable maximum width");
  assert.doesNotMatch(app, /mx-auto flex min-h-full w-full max-w-none flex-col/, "the main report column should not expand to unlimited reading width");

  assert.match(resultPanel, /showStatusHeader = true/, "the status header should be optional so session reports can start with the conclusion");
  assert.match(resultPanel, /showHealthBanner = true/, "the health banner should be optional instead of always preceding the conclusion");
  assert.match(resultPanel, /showHealthBanner && <RunHealthBanner/, "health diagnostics should not be forced above the scientific conclusion");
  assert.doesNotMatch(resultPanel, /max-w-\[1120px\]/, "the final report should align to the surrounding workflow width");

  assert.match(detail, /showStatusHeader=\{false\}/, "completed session reports should not show '工作流完成' before the conclusion");
  assert.match(detail, /showHealthBanner=\{false\}/, "completed session reports should not show health warnings before the conclusion");
});
