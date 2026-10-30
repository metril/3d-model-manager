import { beforeEach, describe, expect, it, vi } from "vitest";

import { installPreloadErrorReload } from "@/lib/preloadError";

describe("installPreloadErrorReload", () => {
  beforeEach(() => sessionStorage.clear());

  it("reloads once, then not again inside the loop window", () => {
    const reload = vi.fn();
    const target = new EventTarget() as unknown as Window;
    installPreloadErrorReload(target, reload);
    target.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
    target.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
