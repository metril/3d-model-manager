// Fails when a literal t("key") / i18nKey="key" used in src/ is missing from
// en.json, or when another locale lacks keys that en.json has.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../src", import.meta.url).pathname;
const flat = (o, p = "") =>
  Object.entries(o).flatMap(([k, v]) =>
    v && typeof v === "object" ? flat(v, `${p}${k}.`) : [`${p}${k}`],
  );
const walk = (d) =>
  readdirSync(d).flatMap((f) => {
    const p = join(d, f);
    return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|jsx?)$/.test(f) && !/\.test\./.test(f) ? [p] : [];
  });

const locales = join(root, "i18n/locales");
const en = new Set(flat(JSON.parse(readFileSync(join(locales, "en.json"), "utf8"))));
const missing = [];
const re = /(?:\bt\(\s*|i18nKey=)["'`]([A-Za-z0-9_.&' -]+)["'`]/g;
for (const file of walk(root)) {
  const src = readFileSync(file, "utf8");
  for (const m of src.matchAll(re)) if (!en.has(m[1])) missing.push(`${file.replace(root, "src")}: ${m[1]}`);
}
for (const f of readdirSync(locales).filter((f) => f.endsWith(".json") && f !== "en.json")) {
  const have = new Set(flat(JSON.parse(readFileSync(join(locales, f), "utf8"))));
  for (const k of en) if (!have.has(k)) missing.push(`${f}: missing ${k}`);
}
if (missing.length) {
  console.error(`i18n: ${missing.length} missing key(s)\n` + missing.join("\n"));
  process.exit(1);
}
console.log(`i18n: ok (${en.size} keys)`);
