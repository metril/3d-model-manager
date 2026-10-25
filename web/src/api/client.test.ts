import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, ENROLL_2FA_EVENT, api } from "@/api/client";

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("api client coded errors", () => {
  it("parses detail:{code,message} bodies", async () => {
    mockFetch(410, { detail: { code: "pre_auth_expired", message: "expired" } });
    await expect(api.post("/auth/2fa/verify", {})).rejects.toMatchObject({ status: 410, code: "pre_auth_expired", detail: "expired" });
  });

  it("keeps plain string details", async () => {
    mockFetch(400, { detail: "nope" });
    await expect(api.get("/x")).rejects.toBeInstanceOf(ApiError);
  });

  it("fires the enrolment event on 403 2fa_enrollment_required", async () => {
    const seen = vi.fn();
    window.addEventListener(ENROLL_2FA_EVENT, seen);
    mockFetch(403, { detail: { code: "2fa_enrollment_required", message: "enrol" } });
    await expect(api.get("/models")).rejects.toMatchObject({ status: 403 });
    expect(seen).toHaveBeenCalledTimes(1);
    window.removeEventListener(ENROLL_2FA_EVENT, seen);
  });
});
