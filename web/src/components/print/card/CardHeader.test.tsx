import { screen } from "@testing-library/react";
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

  it("single state pill; model hidden when it equals the name", async () => {
    const it = a1Mini({}, { gcode_state: "RUNNING" });
    renderCard(<CardHeader printer={{ ...it.printer, name: it.printer.model! }} status={it.status} />);
    await screen.findByRole("link", { name: it.printer.model! });
    expect(screen.getAllByText("Printing")).toHaveLength(1);
    expect(screen.getByText(/0\.4 mm/).textContent).not.toContain(`${it.printer.model} \u00b7`);
  });
});
