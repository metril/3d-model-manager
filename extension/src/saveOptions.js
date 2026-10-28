/**
 * Save options: the popup's collection picker + tag chips. Pure helpers plus
 * a session-cached loader for the two lists (`GET /ext/local-collections`,
 * `GET /ext/tags`) so reopening the popup doesn't refetch every time.
 * `chrome.storage.session` is the cache; it is optional (absent -> uncached).
 */

export const MAX_TAGS = 20;
export const MAX_TAG_LENGTH = 64;
const CACHE_KEY = "options-cache";
const CACHE_TTL_MS = 5 * 60 * 1000;

/**
 * Trims, drops empty/over-long entries, dedupes case-insensitively (first
 * spelling wins) and caps the list, matching the server's limits.
 * @param {unknown} raw
 * @returns {string[]}
 */
export function normalizeTags(raw) {
  const out = [];
  const seen = new Set();
  for (const item of Array.isArray(raw) ? raw : []) {
    const tag = typeof item === "string" ? item.trim() : "";
    const key = tag.toLowerCase();
    if (!tag || tag.length > MAX_TAG_LENGTH || seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(tag);
    if (out.length >= MAX_TAGS) {
      break;
    }
  }
  return out;
}

/** Splits a typed/pasted string on commas/newlines into tag candidates. */
export function splitTagInput(text) {
  return String(text ?? "").split(/[,\n]/);
}

/** The `{collectionId, tags}` argument for `createImport` from raw form values. */
export function buildSaveOptions({ collectionId, tags }) {
  const id = collectionId === "" || collectionId == null ? null : Number(collectionId);
  return {
    collectionId: Number.isInteger(id) ? id : null,
    tags: normalizeTags(tags),
  };
}

/**
 * Both lists, from the session cache when fresh, else the app. A failed
 * fetch yields an empty list for that half (the form still works, just
 * without suggestions) and is NOT cached.
 * @param {{listLocalCollections: Function, listTags: Function}} api
 * @param {{now?: () => number}} [opts]
 * @returns {Promise<{collections: Array<{id: number, name: string}>, tags: string[]}>}
 */
export async function loadOptionLists(api, { now = Date.now } = {}) {
  const session = globalThis.chrome?.storage?.session;
  try {
    const cached = session ? (await session.get(CACHE_KEY))[CACHE_KEY] : null;
    if (cached && now() - cached.at < CACHE_TTL_MS) {
      return { collections: cached.collections, tags: cached.tags };
    }
  } catch {
    // Cache unreadable: fall through to the network.
  }
  const [cols, tags] = await Promise.all([api.listLocalCollections(), api.listTags()]);
  const collections =
    cols.ok && Array.isArray(cols.data)
      ? cols.data.filter((c) => c && Number.isInteger(c.id) && typeof c.name === "string")
      : [];
  const tagNames =
    tags.ok && Array.isArray(tags.data) ? tags.data.map((t) => t && t.name).filter((n) => typeof n === "string") : [];
  if (cols.ok && tags.ok && session) {
    try {
      await session.set({ [CACHE_KEY]: { at: now(), collections, tags: tagNames } });
    } catch {
      // Best-effort cache.
    }
  }
  return { collections, tags: tagNames };
}
