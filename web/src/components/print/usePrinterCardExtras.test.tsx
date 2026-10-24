import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/api/auth";

import { ManagedPrinterCard } from "./ManagedPrinterCard";
import { a1Mini, renderCard } from "./card/testUtils";

const { meBox } = vi.hoisted(() => ({
  meBox: {
    current: {
      id: 1,
      username: "t",
      display_name: null,
      email: null,
      role: "admin",
      permissions: [],
      groups: [],
      has_password: true,
      printer_access: null,
    } as Me,
  },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  const get = (url: string) => {
    if (url === "/auth/me") return Promise.resolve(meBox.current);
    if (url === "/printers") return Promise.resolve([]);
    return Promise.resolve([]);
  };
  return { ...actual, api: { ...actual.api, get, post: vi.fn(), patch: vi.fn() } };
});

describe("ManagedPrinterCard (usePrinterCardExtras)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("M card: camera split button, firmware and history buttons in the footer; Info/Diagnose in the menu", async () => {
    renderCard(<ManagedPrinterCard item={a1Mini()} size="M" />);
    expect(await screen.findByRole("button", { name: "Camera" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Camera options" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Firmware" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Temperature history" })).toBeInTheDocument();
  });

  it("S compact card shows no footer items", async () => {
    renderCard(<ManagedPrinterCard item={a1Mini()} size="S" />);
    expect(await screen.findByRole("button", { name: /Expand/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Firmware" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Camera" })).toBeNull();
  });
});
