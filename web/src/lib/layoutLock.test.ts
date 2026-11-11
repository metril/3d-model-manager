import { beforeEach, describe, expect, it, vi } from "vitest";

import { LAYOUT_LOCK_KEY, getLayoutLocked, reloadLayoutLock, setLayoutLocked } from "./layoutLock";

beforeEach(() => {
  localStorage.clear();
  reloadLayoutLock();
});

describe("layoutLock", () => {
  it("defaults to locked", () => expect(getLayoutLocked()).toBe(true));

  it("persists and reloads the unlocked state", () => {
    setLayoutLocked(false);
    expect(localStorage.getItem(LAYOUT_LOCK_KEY)).toBe("false");
    reloadLayoutLock();
    expect(getLayoutLocked()).toBe(false);
  });

  it("falls back safely when storage throws", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    reloadLayoutLock();
    expect(getLayoutLocked()).toBe(true);
    expect(() => setLayoutLocked(false)).not.toThrow();
    expect(getLayoutLocked()).toBe(false);
    get.mockRestore();
    set.mockRestore();
  });
});
