import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("header brand is reduced to logo and ARCHE only", async () => {
  const header = await readFile(join(here, "Header.tsx"), "utf8");

  assert.match(header, />ARCHE</, "the header should keep the product name");
  assert.match(header, /BenzeneLogo/, "the header should keep the compact product icon");
  assert.doesNotMatch(header, /计算化学工作台/, "the brand area should not include the workbench chip");
  assert.doesNotMatch(header, /v\{info\.version\}|当前部署的镜像版本/, "the brand area should not include version chips");
  assert.doesNotMatch(header, /检索 \/ 假设 \/ 规划 \/ 执行 \/ 反思/, "the brand area should not include the process subtitle");
});
