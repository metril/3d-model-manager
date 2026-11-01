import { afterEach, describe, expect, it, vi } from "vitest";

import { goToModel } from "@/components/orcaslicer/orcaslicerNav";

describe("goToModel from the standalone /slicer tab", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("navigates and focuses a same-origin opener", () => {
    window.history.pushState({}, "", "/slicer");
    const opener = { closed: false, location: { origin: window.location.origin, assign: vi.fn() }, focus: vi.fn() };
    Object.defineProperty(window, "opener", { value: opener, configurable: true });
    goToModel("m");
    expect(opener.location.assign).toHaveBeenCalledWith("/models/m");
    expect(opener.focus).toHaveBeenCalled();
    Object.defineProperty(window, "opener", { value: null, configurable: true });
  });

  it("opens a new tab when there is no opener", () => {
    window.history.pushState({}, "", "/slicer");
    Object.defineProperty(window, "opener", { value: null, configurable: true });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    goToModel("m");
    expect(open).toHaveBeenCalledWith("/models/m", "_blank");
  });
});
