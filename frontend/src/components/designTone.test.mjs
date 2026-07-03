import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const shellFiles = [
  "../index.css",
  "../App.tsx",
  "Header.tsx",
  "QuestionForm.tsx",
  "ExpressionEditor.tsx",
  "HistoryPanel.tsx",
  "DetailView.tsx",
  "ConfigModal.tsx",
  "AgentLoop.tsx",
];

test("workspace shell uses a premium forest-green palette without purple fallback", async () => {
  const sources = await Promise.all(
    shellFiles.map(async (file) => ({
      file,
      source: await readFile(join(here, file), "utf8"),
    })),
  );
  const combined = sources.map(({ source }) => source).join("\n");

  assert.doesNotMatch(combined, /\b(?:indigo|violet|purple)-|#4f46e5|#6366f1/, "purple/indigo makes the scientific workbench feel less premium");
  assert.match(combined, /\b(?:emerald|green)-|#0b1f17|#14532d/, "the primary workbench accent should remain forest green");
  assert.doesNotMatch(combined, /#f8fcfb|#fbfffd|#f0fbf7/, "green-tinted shell backgrounds make the interface feel visually noisy");

  const structuralGreenTokens = combined.match(/\b(?:border|ring|shadow)-(?:teal|emerald|green)-\S+|\bbg-(?:teal|emerald|green)-(?:50|100)\b/g) ?? [];
  assert.ok(
    structuralGreenTokens.length <= 6,
    `expected green to be an accent, found ${structuralGreenTokens.length} structural green tokens`,
  );
});
