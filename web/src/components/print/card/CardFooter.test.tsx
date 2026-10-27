import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CardFooter } from "./CardFooter";
import { renderCard } from "./testUtils";

vi.mock("@/components/print/PrintDialog", () => ({
  PrintDialog: ({ open }: { open: boolean }) => (open ? <div role="dialog">print dialog</div> : null),
}));

describe("CardFooter", () => {
  it("idle: full-width Print opens the dialog", async () => {
    renderCard(<CardFooter canPrint online gcodeState="IDLE" />);
    expect(screen.queryByRole("dialog")).toBeNull();
    const btn = await screen.findByRole("button", { name: "Print" });
    expect(btn.className).toContain("col-span-2");
    fireEvent.click(btn);
    expect(await screen.findByRole("dialog")).toHaveTextContent("print dialog");
  });

  it("no Print button without permission", async () => {
    const { container } = renderCard(<CardFooter canPrint={false} online gcodeState="IDLE" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
    expect(container.querySelector('[data-slot="card-footer"]')).toBeNull();
  });

  it("offline: no Print, shows the unreachable note", async () => {
    renderCard(<CardFooter canPrint online={false} />);
    expect(await screen.findByText("Not reachable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
  });
});
