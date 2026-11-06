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

  it("turns 422 list details into readable text", async () => {
    mockFetch(422, { detail: [{ loc: ["body", "moves", 0, "rel_path"], msg: "String should have at most 1024 characters" }, { loc: ["body"], msg: "bad" }] });
    await expect(api.post("/x", {})).rejects.toMatchObject({
      status: 422,
      detail: "moves.0.rel_path: String should have at most 1024 characters; bad",
    });
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

describe("api client body handling", () => {
  it("returns undefined for an empty 200 body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 200 })));
    await expect(api.post("/x")).resolves.toBeUndefined();
  });

  it("passes the abort signal through and getBlob returns a Blob", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("abc", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const ctrl = new AbortController();
    const blob = await api.getBlob("/spools/export.csv", { signal: ctrl.signal });
    expect(await blob.text()).toBe("abc");
    expect(fetchMock.mock.calls[0][1].signal).toBe(ctrl.signal);
  });

  it("getBlob surfaces API errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 400 })));
    await expect(api.getBlob("/x")).rejects.toMatchObject({ status: 400, detail: "nope" });
  });
});
