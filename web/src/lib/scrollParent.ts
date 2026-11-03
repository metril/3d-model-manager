/** Nearest ancestor of `node` that scrolls vertically (overflow-y auto/scroll/overlay), or null. */
export function findScrollParent(node: HTMLElement | null): HTMLElement | null {
  let el = node?.parentElement ?? null;
  while (el) {
    const overflowY = getComputedStyle(el).overflowY;
    if (overflowY === "auto" || overflowY === "scroll" || overflowY === "overlay") return el;
    el = el.parentElement;
  }
  return null;
}

/** `node`'s top offset within the scroll container's content (or the document when `scrollEl` is null). */
export function offsetInScrollParent(node: HTMLElement, scrollEl: HTMLElement | null): number {
  const top = node.getBoundingClientRect().top;
  if (!scrollEl) return top + window.scrollY;
  return top - scrollEl.getBoundingClientRect().top + scrollEl.scrollTop;
}
