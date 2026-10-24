import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CardFooter } from "./CardFooter";
import { renderCard } from "./testUtils";

vi.mock("@/components/print/PrintDialog", () => ({
  PrintDialog: ({ open }: { open: boolean }) => (open ? <div role="dialog">print dialog</div> : null),
}));

describe("CardFooter", () => {
  it("Print opens the dialog; extra slot renders", async () => {
    renderCard(<CardFooter canPrint online extra={<span>plug</span>} />);
    expect(await screen.findByText("plug")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Print" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("print dialog");
  });

  it("no Print button without permission; disabled when offline", async () => {
    const { unmount } = renderCard(<CardFooter canPrint={false} online extra={<span data-testid="card-footer-probe" />} />);
    await screen.findByTestId("card-footer-probe");
    expect(screen.queryByRole("button", { name: "Print" })).toBeNull();
    unmount();
    renderCard(<CardFooter canPrint online={false} />);
    expect(await screen.findByRole("button", { name: "Print" })).toBeDisabled();
  });
});
