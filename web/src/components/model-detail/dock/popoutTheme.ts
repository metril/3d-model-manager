/** Theme state lives as classes/attributes on <html> (`.dark`, data-theme,
 * data-accent, data-style). A pop-out window has its own <html>, so mirror them. */
const THEME_ATTRS = ["data-theme", "data-accent", "data-style"] as const;

export function copyTheme(from: HTMLElement, to: HTMLElement): void {
  to.className = from.className;
  to.style.colorScheme = from.style.colorScheme;
  for (const attr of THEME_ATTRS) {
    const value = from.getAttribute(attr);
    if (value === null) to.removeAttribute(attr);
    else to.setAttribute(attr, value);
  }
}

/** Mirrors the main document's theme into `target` now and on every change.
 * Returns a disposer that stops observing. */
export function syncThemeInto(target: HTMLElement, source: HTMLElement = document.documentElement): () => void {
  copyTheme(source, target);
  const observer = new MutationObserver(() => copyTheme(source, target));
  observer.observe(source, { attributes: true, attributeFilter: ["class", ...THEME_ATTRS] });
  return () => observer.disconnect();
}
