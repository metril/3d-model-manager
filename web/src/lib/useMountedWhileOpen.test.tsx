import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMountedWhileOpen } from "@/lib/useMountedWhileOpen";

describe("useMountedWhileOpen", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("is false while closed, true on open, and lingers for the close animation", () => {
    const { result, rerender } = renderHook(({ open }) => useMountedWhileOpen(open, 200), { initialProps: { open: false } });
    expect(result.current[0]).toBe(false);
    rerender({ open: true });
    expect(result.current[0]).toBe(true);
    const first = result.current[1];
    rerender({ open: false });
    expect(result.current[0]).toBe(true);
    // Reopening inside the close window is a fresh session (new key).
    rerender({ open: true });
    expect(result.current[1]).toBe(first + 1);
    rerender({ open: false });
    act(() => void vi.advanceTimersByTime(250));
    expect(result.current[0]).toBe(false);
  });
});
