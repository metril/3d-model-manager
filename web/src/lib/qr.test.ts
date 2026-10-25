import { describe, expect, it } from "vitest";

import { qrMatrix } from "@/lib/qr";

describe("qrMatrix", () => {
  it("sizes by version and draws finder patterns", () => {
    const small = qrMatrix("HELLO");
    expect(small).toHaveLength(21);
    expect(small[0].slice(0, 7).every(Boolean)).toBe(true);
    expect(small[3][3]).toBe(true);
    const url = qrMatrix(`otpauth://totp/x:y?secret=${"A".repeat(32)}&issuer=x&algorithm=SHA1&digits=6&period=30`);
    expect(url.length).toBeGreaterThan(21);
    expect((url.length - 17) % 4).toBe(0);
  });
  it("rejects oversized payloads", () => {
    expect(() => qrMatrix("x".repeat(400))).toThrow();
  });
});
