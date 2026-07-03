import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("empty research form is centered with a typewriter prompt", async () => {
  const app = await readFile(join(here, "../App.tsx"), "utf8");
  const form = await readFile(join(here, "QuestionForm.tsx"), "utf8");
  const css = await readFile(join(here, "../index.css"), "utf8");

  assert.match(app, /mx-auto flex min-h-full w-full max-w-\[1160px\] flex-col/, "the content surface should fill the pane so the empty form can center vertically");
  assert.match(app, /flex flex-1 items-center justify-center/, "the empty-session form should sit in the middle of the work area");
  assert.doesNotMatch(app, /max-w-5xl pt-6/, "the empty-session form should not be pinned near the top");

  assert.match(form, /今天研究什么/, "the form should introduce the input with the requested prompt");
  assert.match(form, /question-typewriter/, "the prompt should use the typewriter animation class");
  assert.match(css, /@keyframes arche-typewriter/, "the typewriter effect should be implemented in shared CSS");
  assert.match(css, /\.question-typewriter/, "the form should have a reusable typewriter class");
});
