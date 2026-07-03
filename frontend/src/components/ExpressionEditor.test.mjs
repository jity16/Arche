import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("ExpressionEditor renders input and preview without a formatting toolbar", async () => {
  const source = await readFile(join(here, "ExpressionEditor.tsx"), "utf8");

  assert.doesNotMatch(source, /const GROUPS/);
  assert.doesNotMatch(source, /<button/);
  assert.doesNotMatch(source, /onMouseDown/);
  assert.doesNotMatch(source, /工具栏/);
});
