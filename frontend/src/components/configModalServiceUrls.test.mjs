import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

test("config modal exposes expert model and Gaussian service URLs", async () => {
  const modal = await readFile(join(here, "ConfigModal.tsx"), "utf8");
  const api = await readFile(join(here, "../api.ts"), "utf8");
  const types = await readFile(join(here, "../types.ts"), "utf8");

  assert.match(types, /expertBaseUrl:\s*string;/, "runtime config should expose the expert model URL");
  assert.match(types, /gaussianBaseUrl:\s*string;/, "runtime config should expose the Gaussian service URL");
  assert.match(api, /expertBaseUrl\?:\s*string;/, "config patch should accept expertBaseUrl");
  assert.match(api, /gaussianBaseUrl\?:\s*string;/, "config patch should accept gaussianBaseUrl");
  assert.match(modal, /Field label="专家模型地址"/, "settings should render an expert model URL field");
  assert.match(modal, /Field label="Gaussian 服务地址"/, "settings should render a Gaussian URL field");
  assert.match(
    modal,
    /const defaultExpertBaseUrl =[\s\S]*lyq-test-k62j9-13402-worker-0\.liyuqiang\/18081\/v1";/,
    "expert URL field should define the new default address",
  );
  assert.match(
    modal,
    /const defaultGaussianBaseUrl =[\s\S]*lyq-test-r8488-25714-worker-0\.liyuqiang\/vscode\/proxy\/18081";/,
    "Gaussian URL field should define the new default address",
  );
  assert.match(modal, /placeholder=\{defaultExpertBaseUrl\}/, "expert URL field should use the shared default value");
  assert.match(modal, /placeholder=\{defaultGaussianBaseUrl\}/, "Gaussian URL field should use the shared default value");
  assert.match(
    modal,
    /const patch: ConfigPatch = \{[\s\S]*expertBaseUrl[\s\S]*gaussianBaseUrl[\s\S]*\};/,
    "save payload should include expert and Gaussian URLs",
  );
});
