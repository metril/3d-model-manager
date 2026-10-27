import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/api/auth";

import { ManagedPrinterCard } from "./ManagedPrinterCard";
import { a1Mini, renderCard } from "./card/testUtils";

const { meBox, getCalls } = vi.hoisted(() => ({
  getCalls: [] as string[],
  meBox: {
    current: {
      id: 1,
      username: "t",
      display_name: null,
      email: null,
      role: "admin",
      permissions: ["admin", "printers:print", "printers:control", "settings:write"],
      groups: [],
      has_password: true,
      printer_access: null,
    } as Me,
  },
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  const get = (url: string) => {
    getCalls.push(url);
    if (url === "/auth/me") return Promise.resolve(meBox.current);
    if (url === "/printers/detections/summary") return Promise.resolve({ printers: {} });
    if (url === "/printers/plugs/summary") return Promise.resolve({ plugs: {} });
    return Promise.resolve([]);
  };
  return { ...actual, api: { ...actual.api, get, post: vi.fn(), patch: vi.fn() } };
});

describe("ManagedPrinterCard (usePrinterCardExtras)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCalls.length = 0;
  });

  it("M card: camera split button + More menu (firmware, history) in the footer toolbar", async () => {
    renderCard(<ManagedPrinterCard item={a1Mini()} size="M" />);
    expect(await screen.findByRole("button", { name: "Camera" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Camera options" })).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("button", { name: "More" }), { button: 0, ctrlKey: false });
    expect(await screen.findByRole("menuitem", { name: "Firmware" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Temperature history" })).toBeInTheDocument();
  });

  it("S compact card shows no footer items", async () => {
    renderCard(<ManagedPrinterCard item={a1Mini()} size="S" />);
    expect(await screen.findByRole("button", { name: /Expand/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Camera" })).toBeNull();
  });

  it("3 cards: no per-printer /status, /detection or /plug polls; one shared request each", async () => {
    const items = [a1Mini(), { ...a1Mini(), printer: { ...a1Mini().printer, id: 2 } }, { ...a1Mini(), printer: { ...a1Mini().printer, id: 3 } }];
    renderCard(
      <>
        {items.map((it) => (
          <ManagedPrinterCard key={it.printer.id} item={it} size="M" />
        ))}
      </>,
    );
    await waitFor(() => expect(getCalls.filter((u) => u === "/printers/plugs/summary")).toHaveLength(1));
    await waitFor(() => expect(getCalls.filter((u) => u === "/printers/detections/summary")).toHaveLength(1));
    expect(getCalls.filter((u) => /\/printers\/\d+\/(status|detection|plugs?)$/.test(u))).toHaveLength(0);
  });
});
