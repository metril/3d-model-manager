import { describe, expect, it } from "vitest";

import { ApiError } from "@/api/client";
import { controlErrorMessage } from "@/api/printerControl";

describe("controlErrorMessage", () => {
  it("maps known control codes", () => {
    expect(controlErrorMessage(new ApiError(409, "x", "dev_mode_off"))).toMatch(/Developer LAN mode/);
    expect(controlErrorMessage(new ApiError(409, "x", "offline"))).toMatch(/offline/);
    expect(controlErrorMessage(new ApiError(409, "x", "cold_extrude"))).toMatch(/170/);
    expect(controlErrorMessage(new ApiError(422, "x", "unsupported"))).toMatch(/does not support/);
  });
  it("falls back to detail, then a generic message", () => {
    expect(controlErrorMessage(new ApiError(422, "bad value", "invalid"))).toBe("bad value");
    expect(controlErrorMessage(new Error("boom"))).toBe("Printer command failed.");
  });
});
