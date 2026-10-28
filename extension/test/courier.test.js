import { test } from "node:test";
import assert from "node:assert/strict";

import { decidePush, hashToken, pickCookie, pickCookieValue, shouldPush } from "../src/courier.js";

test("hashToken: stable for the same input, differs for different input", async () => {
  const a1 = await hashToken("secret-cookie-value");
  const a2 = await hashToken("secret-cookie-value");
  const b = await hashToken("different-value");
  assert.equal(a1, a2);
  assert.notEqual(a1, b);
  assert.match(a1, /^[0-9a-f]{64}$/);
});

test("shouldPush: false for an empty/missing current value", async () => {
  assert.equal(await shouldPush("", "anyhash"), false);
  assert.equal(await shouldPush(null, "anyhash"), false);
  assert.equal(await shouldPush(undefined, null), false);
});

test("shouldPush: false when the current value's hash matches lastPushedHash", async () => {
  const value = "cookie-value-123";
  const hash = await hashToken(value);
  assert.equal(await shouldPush(value, hash), false);
});

test("shouldPush: true when the current value's hash differs from lastPushedHash", async () => {
  assert.equal(await shouldPush("new-cookie-value", null), true);
  assert.equal(await shouldPush("new-cookie-value", await hashToken("old-cookie-value")), true);
});

test("pickCookieValue: null for an empty array", () => {
  assert.equal(pickCookieValue([]), null);
});

test("pickCookieValue: null when nothing has a non-empty value", () => {
  assert.equal(pickCookieValue([{ value: "" }, { value: "" }]), null);
});

test("pickCookieValue: skips an empty-value entry and returns the first non-empty value", () => {
  assert.equal(
    pickCookieValue([{ domain: "www.makerworld.com", value: "" }, { domain: "makerworld.com", value: "tok-abc" }]),
    "tok-abc"
  );
});

test("pickCookieValue: prefers the exact apex domain regardless of array order", () => {
  const a = { domain: "www.makerworld.com", value: "first-value" };
  const b = { domain: ".makerworld.com", value: "second-value" };
  assert.equal(pickCookieValue([a, b]), "second-value");
  assert.equal(pickCookieValue([b, a]), "second-value");
});

test("pickCookie: skips expired cookies", () => {
  const now = 1000;
  assert.equal(pickCookie([{ value: "old", expirationDate: 999 }], "makerworld.com", now), null);
  assert.equal(
    pickCookie([{ value: "old", expirationDate: 999 }, { value: "live", expirationDate: 2000 }], "makerworld.com", now).value,
    "live",
  );
});

test("pickCookie: order is exact domain, then hostOnly, then latest expiry, then value (deterministic)", () => {
  const base = { domain: "x.makerworld.com" };
  assert.equal(pickCookie([{ ...base, value: "a" }, { ...base, value: "b", hostOnly: true }]).value, "b");
  assert.equal(
    pickCookie([{ ...base, value: "a", expirationDate: 5e9 }, { ...base, value: "b", expirationDate: 6e9 }]).value,
    "b",
  );
  assert.equal(pickCookie([{ ...base, value: "a", expirationDate: 5e9 }, { ...base, value: "z" }]).value, "a"); // persistent beats session
  assert.equal(pickCookie([{ ...base, value: "b" }, { ...base, value: "a" }]).value, "a");
  assert.equal(pickCookie([{ ...base, value: "a" }, { ...base, value: "b" }]).value, "a");
});

test("decidePush: hashes once and returns the hash for persisting", async () => {
  const r = await decidePush("cookie", null);
  assert.equal(r.push, true);
  assert.equal(r.hash, await hashToken("cookie"));
  assert.deepEqual(await decidePush("cookie", r.hash), { push: false, hash: r.hash });
  assert.deepEqual(await decidePush("", r.hash), { push: false, hash: null });
});
