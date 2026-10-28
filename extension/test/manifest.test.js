import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("manifest.json", root), "utf8"));

test("manifest: unified name, Firefox gecko id/min version, Chrome min version", () => {
  assert.equal(manifest.name, "3D Model Manager");
  assert.equal(manifest.browser_specific_settings.gecko.id, "tdmm-extension@metril.github.io");
  assert.equal(manifest.browser_specific_settings.gecko.strict_min_version, "115.0");
  assert.equal(manifest.minimum_chrome_version, "121");
});

test("manifest: background declares both service_worker (Chrome) and scripts (Firefox)", () => {
  assert.equal(manifest.background.service_worker, "src/background.js");
  assert.deepEqual(manifest.background.scripts, ["src/background.js"]);
  assert.ok(manifest.permissions.includes("webNavigation"));
});

test("manifest: 16/32/48/128 icons exist on disk and are used for the action too", () => {
  for (const size of ["16", "32", "48", "128"]) {
    for (const map of [manifest.icons, manifest.action.default_icon]) {
      assert.ok(map[size], `icon ${size} missing in manifest`);
      assert.ok(existsSync(new URL(map[size], root)), `${map[size]} not found`);
    }
  }
});

test("release workflow zips the icons directory", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/release.yml", import.meta.url), "utf8");
  assert.match(workflow, /zip -r -X [^\n]*manifest\.json src icons/);
});
