import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("research timeline presents independent process cards with a right-side file preview inspector", async () => {
  const source = await readFile(join(here, "ResearchTimeline.tsx"), "utf8");

  assert.match(source, /ProcessInspector/, "a selected process should open in a dedicated inspector instead of inline expansion");
  assert.match(source, /selectedIndex/, "the timeline should track the selected process card");
  assert.match(source, /lg:grid-cols-\[minmax\(0,0\.86fr\)_minmax\(360px,0\.72fr\)\]/, "desktop layout should reserve a right-side preview area");
  assert.match(source, /流程文件/, "the inspector should expose the files related to the selected process");
  assert.match(source, /预览/, "the inspector should show a readable preview area");
  assert.match(source, /aria-pressed=\{selected\}/, "process cards should expose selected state for assistive tech");

  assert.doesNotMatch(source, /setShowDetail/, "stage details should not expand inside the timeline row");
  assert.doesNotMatch(source, /查看该步完整内容/, "the old inline drill-down control should be replaced by card selection");
});
