import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { closePrint, getPrintRequest, openPrint, usePrintDialogRequest } from "@/components/print/printDialogStore";

afterEach(() => closePrint());

describe("printDialogStore", () => {
  it("opens and closes a request, notifying subscribers", () => {
    const { result } = renderHook(() => usePrintDialogRequest());
    expect(result.current).toBeNull();
    act(() => openPrint({ fileId: 3, plate: 2 }));
    expect(result.current).toEqual({ fileId: 3, plate: 2 });
    expect(getPrintRequest()).toEqual({ fileId: 3, plate: 2 });
    act(() => closePrint());
    expect(result.current).toBeNull();
  });
});
