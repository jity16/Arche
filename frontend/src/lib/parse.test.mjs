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

const { classifyRunTone, diagnose } = await importTs("parse.ts");

test("classifies partial_success independently of exit code", () => {
  assert.equal(classifyRunTone("partial_success", 0), "partial");
});

test("diagnose surfaces partial_success as a warning status", () => {
  const diag = diagnose("", 0, "partial_success");
  assert.equal(diag.tone, "warn");
  assert.match(diag.title, /部分成功/);
});
