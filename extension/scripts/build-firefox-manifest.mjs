#!/usr/bin/env node
// Usage: node build-firefox-manifest.mjs <out.json>
// Derives the Firefox manifest from the Chrome one: Firefox MV3 wants
// background.scripts, Chrome rejects it (and wants service_worker).
import { readFileSync, writeFileSync } from "node:fs";

export function toFirefox(manifest) {
  const { service_worker, ...rest } = manifest.background;
  return { ...manifest, background: { ...rest, scripts: [service_worker] } };
}

const out = process.argv[2];
if (out) {
  const src = new URL("../manifest.json", import.meta.url);
  const m = JSON.parse(readFileSync(src, "utf8"));
  writeFileSync(out, JSON.stringify(toFirefox(m), null, 2) + "\n");
}
