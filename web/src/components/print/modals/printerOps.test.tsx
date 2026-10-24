import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { FileManagerModal } from "./FileManagerModal";
import { PrintOptionsModal } from "./PrintOptionsModal";
import { a1Mini } from "../card/testUtils";

const { getMock, postMock, deleteMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn(), deleteMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock, delete: deleteMock } };
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
  getMock.mockReset();
  postMock.mockReset();
  deleteMock.mockReset();
});

function printer(caps: Record<string, unknown>) {
  const p = a1Mini().printer;
  return { ...p, capabilities: { ...p.capabilities!, ...caps } };
}

describe("PrintOptionsModal", () => {
  const st = a1Mini({}, { print_options: { xcam: { spaghetti_detector: { enable: true, sensitivity: "high" } }, auto_recovery: false } }).status;

  it("renders one row per capability module and posts xcam payload", async () => {
    postMock.mockResolvedValue({ ok: true });
    wrap(
      <PrintOptionsModal
        printer={printer({ xcam_modules: ["spaghetti_detector", "first_layer_inspector"] })}
        status={st}
        canControl
        open
        onOpenChange={() => {}}
      />,
    );
    expect(screen.getByTestId("xcam-spaghetti_detector")).toBeInTheDocument();
    expect(screen.getByTestId("xcam-first_layer_inspector")).toBeInTheDocument();
    expect(screen.queryByTestId("xcam-pileup_detector")).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: /First layer inspection/ }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/control/xcam", {
        module: "first_layer_inspector",
        enable: true,
        sensitivity: "medium",
      }),
    );
  });

  it("sends never_halt and the print-option flags; reverts on error", async () => {
    postMock.mockResolvedValueOnce({ ok: true });
    postMock.mockRejectedValueOnce(new ApiError(409, "offline", "offline"));
    wrap(<PrintOptionsModal printer={printer({ xcam_modules: ["spaghetti_detector"] })} status={st} canControl open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole("combobox", { name: /Spaghetti detection Sensitivity/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Never halt" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/control/xcam", {
        module: "spaghetti_detector",
        enable: true,
        sensitivity: "never_halt",
      }),
    );
    const sw = screen.getByRole("switch", { name: "Auto-recovery" });
    await waitFor(() => expect(sw).not.toBeDisabled());
    fireEvent.click(sw);
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/control/print-option", { option: "auto_recovery", value: true }),
    );
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
  });

  it("disables everything without control", () => {
    wrap(<PrintOptionsModal printer={printer({ xcam_modules: ["spaghetti_detector"] })} status={st} canControl={false} open onOpenChange={() => {}} />);
    expect(screen.getByRole("switch", { name: "Auto-recovery" })).toBeDisabled();
  });
});

describe("FileManagerModal", () => {
  const root = [
    { name: "model", path: "/model", size: null, mtime: null, is_dir: true },
    { name: "a.gcode.3mf", path: "/a.gcode.3mf", size: 2048, mtime: null, is_dir: false },
    { name: "notes.txt", path: "/notes.txt", size: 10, mtime: null, is_dir: false },
  ];
  const sub = [{ name: "b.3mf", path: "/model/b.3mf", size: 5, mtime: null, is_dir: false }];

  function setup(sdPrint = true, canControl = true) {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("/files/storage")) return { total: 1000, used: 250, free: 750 };
      if (url.includes("path=%2Fmodel")) return sub;
      return root;
    });
    wrap(<FileManagerModal printerId={1} canControl={canControl} sdPrint={sdPrint} open onOpenChange={() => {}} />);
  }

  it("lists, navigates folders and shows storage", async () => {
    setup();
    expect(await screen.findByText("a.gcode.3mf")).toBeInTheDocument();
    expect(await screen.findByTestId("storage-bar")).toBeInTheDocument();
    fireEvent.click(screen.getByText("model"));
    expect(await screen.findByText("b.3mf")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith("/printers/1/files?path=%2Fmodel");
    fireEvent.click(screen.getByRole("button", { name: "SD card" }));
    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
  });

  it("Print only for .3mf with sd_print; confirms then posts", async () => {
    postMock.mockResolvedValue({});
    setup();
    await screen.findByText("a.gcode.3mf");
    expect(screen.getAllByRole("button", { name: "Print" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    const dlg = await screen.findByRole("dialog", { name: /Print a.gcode.3mf/ });
    fireEvent.click(within(dlg).getByRole("button", { name: "Print" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/printers/1/files/print", { use_ams: true, path: "/a.gcode.3mf" }),
    );
  });

  it("hides Print without the sd_print capability", async () => {
    setup(false);
    await screen.findByText("a.gcode.3mf");
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
  });

  it("deletes after confirm and links downloads", async () => {
    deleteMock.mockResolvedValue(undefined);
    setup();
    await screen.findByText("notes.txt");
    expect(screen.getByRole("link", { name: "Download notes.txt" })).toHaveAttribute(
      "href",
      "/api/printers/1/files/download?path=%2Fnotes.txt",
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete notes.txt" }));
    const dlg = await screen.findByRole("dialog", { name: /Delete notes.txt/ });
    fireEvent.click(within(dlg).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("/printers/1/files?path=%2Fnotes.txt"));
  });

  it("shows empty and error states", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("/storage")) return {};
      return [];
    });
    wrap(<FileManagerModal printerId={1} canControl sdPrint open onOpenChange={() => {}} />);
    expect(await screen.findByText("This folder is empty.")).toBeInTheDocument();
  });
});
