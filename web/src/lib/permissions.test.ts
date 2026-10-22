import { describe, expect, it } from "vitest";

import type { Me } from "@/api/auth";
import { can, canPrinter } from "@/lib/permissions";

const base: Me = {
  id: 1,
  username: "u",
  display_name: null,
  email: null,
  role: "member",
  permissions: [],
  groups: [],
  has_password: true,
  printer_access: {},
};
const adminMe: Me = {
  ...base,
  role: "admin",
  permissions: ["admin", "billing:manage", "printers:control"],
  printer_access: null,
};
const viewerMe: Me = { ...base, role: "viewer", permissions: [] };
const memberMe: Me = { ...base };

describe("permissions", () => {
  it("admin can everything", () => expect(can(adminMe, "billing:manage")).toBe(true));
  it("viewer cannot write", () => expect(can(viewerMe, "library:write")).toBe(false));
  it("undefined me can nothing", () => expect(can(undefined, "admin")).toBe(false));
  it("canPrinter needs perm and acl", () => {
    const me: Me = {
      ...memberMe,
      permissions: ["printers:print"],
      printer_access: { "3": { print: true, control: false } },
    };
    expect(canPrinter(me, 3, "print")).toBe(true);
    expect(canPrinter(me, 3, "control")).toBe(false);
    expect(canPrinter(me, 4, "print")).toBe(false);
    expect(canPrinter(adminMe, 99, "control")).toBe(true);
  });
});
