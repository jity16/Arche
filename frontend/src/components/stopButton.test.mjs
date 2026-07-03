import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("stop button calls the backend cancellation endpoint for the active run", async () => {
  const app = await readFile(join(here, "../App.tsx"), "utf8");
  const api = await readFile(join(here, "../api.ts"), "utf8");

  assert.match(api, /cancelRun:\s*async\s*\(id: string\)/, "API client should expose an explicit cancelRun method");
  assert.match(api, /\/api\/runs\/\$\{encodeURIComponent\(id\)\}\/cancel/, "cancelRun should target the run cancellation endpoint");
  assert.match(app, /activeRunIdRef/, "App should keep the current run id outside render state for stop clicks");
  assert.match(app, /archeApi\s*\.\s*cancelRun\(/, "onStop should cancel the backend run, not only abort the stream");
  assert.match(
    app,
    /\.cancelRun\(runId\)[\s\S]*?\.then\(\(\) => \{[\s\S]*?abortRef\.current\?\.abort\(\);/,
    "the stream should be aborted only after the backend accepted cancellation",
  );
});
