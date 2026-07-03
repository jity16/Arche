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

const { renderMarkdownMath } = await importTs("markdown.ts");

test("renders markdown blocks with inline formulas", () => {
  const html = renderMarkdownMath("**重点** $x^2$\n\n- 第一项\n- 第二项");

  assert.match(html, /<strong>重点<\/strong>/);
  assert.match(html, /class="katex"/);
  assert.match(html, /<ul>/);
  assert.match(html, /<li>第一项<\/li>/);
  assert.match(html, /<li>第二项<\/li>/);
});

test("escapes raw html while preserving inline markdown", () => {
  const html = renderMarkdownMath("<img src=x onerror=alert(1)> and `a < b`");

  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /<code>a &lt; b<\/code>/);
});
