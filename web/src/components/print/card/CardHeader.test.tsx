import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardHeader } from "./CardHeader";
import { a1Mini, renderCard, x1c } from "./testUtils";

const { postMock, canBox } = vi.hoisted(() => ({ postMock: vi.fn(), canBox: { current: true } }));

vi.mock("@/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/auth")>()),
  useAuth: () => ({ can: () => canBox.current, canPrinter: () => true }),
}));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, post: postMock, get: vi.fn().mockResolvedValue([]) } };
});

function openMenu() {
  fireEvent.pointerDown(screen.getByRole("button", { name: "Printer menu" }), { button: 0, ctrlKey: false });
}

describe("CardHeader", () => {
  beforeEach(() => {
    postMock.mockReset();
    canBox.current = true;
  });

  it("shows the model image, name link, nozzle size and total hours", async () => {
    const it = a1Mini();
    const { container } = renderCard(<CardHeader printer={it.printer} status={it.status} />);
    const link = await screen.findByRole("link", { name: "A1 Mini" });
    expect(link).toHaveAttribute("href", "/printers/1");
    expect(container.querySelector("img")).toHaveAttribute("src", "/img/printers/a1mini.png");
    expect(screen.getByText(/0\.4 mm/)).toBeInTheDocument();
    expect(screen.getByText(/123 h printed/)).toBeInTheDocument();
  });

  it("uses the X1C image", async () => {
    const it = x1c();
    const { container } = renderCard(<CardHeader printer={it.printer} status={it.status} />);
    await screen.findByRole("link", { name: "X1C" });
    expect(container.querySelector("img")).toHaveAttribute("src", "/img/printers/x1c.png");
  });

  it("menu: Info / Diagnose call the slot callbacks", async () => {
    const onInfo = vi.fn();
    const onDiagnose = vi.fn();
    const it = a1Mini();
    renderCard(<CardHeader printer={it.printer} status={it.status} onInfo={onInfo} onDiagnose={onDiagnose} />);
    await screen.findByRole("link", { name: "A1 Mini" });
    openMenu();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Info" }));
    expect(onInfo).toHaveBeenCalled();
    openMenu();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Diagnose" }));
    expect(onDiagnose).toHaveBeenCalled();
  });

  it("menu: Reconnect probes the printer; admin items hidden without settings:write", async () => {
    postMock.mockResolvedValue({ ok: true, detail: "ok", gcode_state: "IDLE" });
    const it = a1Mini();
    renderCard(<CardHeader printer={it.printer} status={it.status} onInfo={() => {}} />);
    await screen.findByRole("link", { name: "A1 Mini" });
    openMenu();
    fireEvent.click(await screen.findByRole("menuitem", { name: "Reconnect" }));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/printers/1/test"));
  });

  it("hides Edit/Diagnose/Delete for non-admins", async () => {
    canBox.current = false;
    const it = a1Mini();
    renderCard(<CardHeader printer={it.printer} status={it.status} onDiagnose={() => {}} />);
    await screen.findByRole("link", { name: "A1 Mini" });
    openMenu();
    await screen.findByRole("menuitem", { name: "Force refresh" });
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Diagnose" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Edit" })).toBeNull();
  });
});
