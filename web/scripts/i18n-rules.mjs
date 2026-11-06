// Plural-form rules for the locale checker (shared with its unit test).
const SUFFIX = /_(zero|one|two|few|many|other)$/;

/** Problems in a (nested) locale object: a plural form without its sibling
 * `_one`/`_other` counterpart, or a `{{count}}` string with no plural forms. */
export function pluralProblems(obj, prefix = "") {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === "object") {
      out.push(...pluralProblems(v, `${prefix}${k}.`));
      continue;
    }
    const path = `${prefix}${k}`;
    if (/_(one|other)$/.test(k)) {
      const base = k.replace(/_(one|other)$/, "");
      const sibling = base + (k.endsWith("_one") ? "_other" : "_one");
      if (!(sibling in obj)) out.push(`${path}: missing ${sibling}`);
    } else if (!SUFFIX.test(k) && typeof v === "string" && v.includes("{{count}}")) {
      if (!(`${k}_one` in obj) || !(`${k}_other` in obj)) out.push(`${path}: {{count}} without _one/_other`);
    }
  }
  return out;
}
