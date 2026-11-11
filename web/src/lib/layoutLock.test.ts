import { beforeEach, describe, expect, it } from "vitest";

import { getLayoutLocked, reloadLayoutLock, setLayoutLocked } from "./layoutLock";

beforeEach(() => reloadLayoutLock());

describe("layoutLock", () => {
  it("defaults to locked", () => expect(getLayoutLocked()).toBe(true));

  it("never persists the unlocked state", () => {
    setLayoutLocked(false);
    expect(getLayoutLocked()).toBe(false);
    expect(Object.keys(localStorage).filter((k) => k.includes("ayoutLock"))).toEqual([]);
  });
});
