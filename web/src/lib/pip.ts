/** Document Picture-in-Picture helpers (Chromium). Absent elsewhere, where
 * the in-app overlay is the only mode. */
interface DocPiP {
  requestWindow(opts?: { width?: number; height?: number }): Promise<Window>;
}

function api(): DocPiP | undefined {
  return (window as unknown as { documentPictureInPicture?: DocPiP }).documentPictureInPicture;
}

export function pipSupported(): boolean {
  return typeof window !== "undefined" && api() != null;
}

/** Moves `el` into a PiP window (with the page's styles) and back to its
 * original place on close. Resolves with the PiP window, or null when
 * unavailable/refused. */
export async function popOutElement(el: HTMLElement, title: string, width: number, height: number): Promise<Window | null> {
  const pip = api();
  if (!pip) return null;
  try {
    const win = await pip.requestWindow({ width, height });
    win.document.title = title;
    for (const node of document.querySelectorAll('link[rel="stylesheet"], style')) {
      win.document.head.append(node.cloneNode(true));
    }
    win.document.body.style.cssText = "margin:0;background:#000;height:100vh";
    const parent = el.parentElement;
    const next = el.nextSibling;
    win.document.body.append(el);
    win.addEventListener("pagehide", () => {
      parent?.insertBefore(el, next);
    });
    return win;
  } catch {
    return null;
  }
}
