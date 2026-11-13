#!/usr/bin/env node
// Usage: node build-safari-manifest.mjs <out.json>
// Safari manifest: the Firefox transform (background.scripts + type module,
// no service_worker) minus keys Safari's converter flags as unsupported.
// `webNavigation` is dropped too: Safari lacks it, and the code already
// feature-guards every use (background.js, exec.js).
import { readFileSync, writeFileSync } from "node:fs";
import { toFirefox } from "./build-firefox-manifest.mjs";

export function toSafari(manifest) {
  const { browser_specific_settings, minimum_chrome_version, ...rest } = toFirefox(manifest);
  return { ...rest, permissions: (rest.permissions || []).filter((p) => p !== "webNavigation") };
}

const out = process.argv[2];
if (out) {
  const src = new URL("../manifest.json", import.meta.url);
  const m = JSON.parse(readFileSync(src, "utf8"));
  writeFileSync(out, JSON.stringify(toSafari(m), null, 2) + "\n");
}
