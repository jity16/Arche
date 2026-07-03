import test from "node:test";
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "../..");

async function importTs(relativePath) {
  const sourcePath = join(here, relativePath);
  const source = await readFile(sourcePath, "utf8");
  const js = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2020,
    },
  }).outputText;

  const outDir = join(root, ".test-tmp");
  await mkdir(outDir, { recursive: true });
  const outPath = join(outDir, `${relativePath.replace(/[^\w.-]/g, "_")}.mjs`);
  await writeFile(outPath, js);
  return import(pathToFileURL(outPath).href);
}

const { syncHistoryItemFromRun } = await importTs("historyState.ts");

test("syncs a completed detail record into a stale running history item", () => {
  const items = [
    {
      id: "run-1",
      createdAt: 1783046898053,
      question: "预测 H2O",
      exitCode: null,
      status: "running",
    },
    {
      id: "run-2",
      createdAt: 1782999776405,
      question: "other",
      exitCode: 0,
      status: "completed",
    },
  ];

  const next = syncHistoryItemFromRun(items, {
    id: "run-1",
    createdAt: 1783046898053,
    question: "预测 H2O",
    exitCode: 0,
    status: "completed",
    stdout: "",
    stderr: "",
  });

  assert.deepEqual(next[0], {
    id: "run-1",
    createdAt: 1783046898053,
    question: "预测 H2O",
    exitCode: 0,
    status: "completed",
  });
  assert.deepEqual(next[1], items[1]);
});
