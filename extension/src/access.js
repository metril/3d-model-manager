/**
 * Host-permission helpers. Chrome grants the manifest's `host_permissions`
 * at install; Firefox (MV3) grants nothing by default, and the app's own
 * origin is always an OPTIONAL permission. So the popup and options page
 * check `permissions.contains` and offer a "Grant access" button (the
 * request must run inside a user gesture).
 */

import { GALLERY_HOST_PATTERNS } from "./detect.js";

/** `<origin>/*` for the configured app URL, or `null` when unset/invalid. */
export function appOriginPattern(appBaseUrl) {
  try {
    return `${new URL(appBaseUrl).origin}/*`;
  } catch {
    return null;
  }
}

/** Every origin the extension needs: the app's plus the gallery hosts. */
export function requiredOrigins(appBaseUrl) {
  const app = appOriginPattern(appBaseUrl);
  return app ? [app, ...GALLERY_HOST_PATTERNS] : [...GALLERY_HOST_PATTERNS];
}

/**
 * The subset of `requiredOrigins` not currently granted. Never throws -- a
 * failing `permissions.contains` counts as "missing".
 * @param {string} appBaseUrl
 * @returns {Promise<string[]>}
 */
export async function missingOrigins(appBaseUrl) {
  const missing = [];
  for (const origin of requiredOrigins(appBaseUrl)) {
    try {
      if (!(await chrome.permissions.contains({ origins: [origin] }))) {
        missing.push(origin);
      }
    } catch {
      missing.push(origin);
    }
  }
  return missing;
}

/**
 * Requests the given (already computed) missing origins in one prompt. MUST
 * be called synchronously from a user gesture (a click handler): Firefox
 * drops the gesture across an `await`, so `missing` is computed at render time.
 * @param {string[]} missing
 * @returns {Promise<boolean>} true when nothing is missing afterwards
 */
export async function grantAccess(missing) {
  if (missing.length === 0) {
    return true;
  }
  try {
    return Boolean(await chrome.permissions.request({ origins: missing }));
  } catch {
    return false;
  }
}

/** Adds a "Grant access" button to `container` when anything is missing. */
export async function renderGrantAccess(container, appBaseUrl, onGranted) {
  const missing = await missingOrigins(appBaseUrl);
  if (missing.length === 0) {
    return false;
  }
  const button = document.createElement("button");
  button.type = "button";
  button.id = "grant-access";
  button.textContent = "Grant access";
  button.title = `Needed: ${missing.join(", ")}`;
  button.addEventListener("click", async () => {
    // FIRST statement: no awaits before the request (Firefox user gesture).
    const request = chrome.permissions.request({ origins: missing });
    const granted = await Promise.resolve(request).then(Boolean, () => false);
    if (granted) {
      button.remove();
      onGranted?.();
    }
  });
  container.append(button);
  return true;
}
