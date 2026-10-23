import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CardFooter } from "./CardFooter";
import { renderCard } from "./testUtils";

vi.mock("@/components/print/PrintDialog", () => ({
  PrintDialog: ({ open }: { open: boolean }) => (open ? <div role="dialog">print dialog</div> : null),
}));

describe("CardFooter", () => {
  it("camera links to the detail page; Print opens the dialog; extra slot renders", async () => {
    renderCard(<CardFooter printerId={3} canPrint online extra={<span>plug</span>} />);
    expect(await screen.findByRole("link", { name: "Camera" })).toHaveAttribute("href", "/printers/3");
    expect(screen.getByText("plug")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("print dialog");
  });

  it("no Print button without permission; disabled when offline", async () => {
    const { unmount } = renderCard(<CardFooter printerId={3} canPrint={false} online />);
    await screen.findByRole("link", { name: "Camera" });
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
    unmount();
    renderCard(<CardFooter printerId={3} canPrint online={false} />);
    expect(await screen.findByRole("button", { name: "Print" })).toBeDisabled();
  });
});
