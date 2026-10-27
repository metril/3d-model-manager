import { render, screen } from "@testing-library/react";
import { InboxIcon } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/ui/empty-state";

describe("EmptyState", () => {
  it("renders the icon, one line and the CTA", () => {
    const { container } = render(
      <EmptyState icon={InboxIcon} title="Nothing here" action={<button type="button">Do it</button>} />,
    );
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Do it" })).toBeInTheDocument();
    expect(container.querySelector("svg.size-8")).not.toBeNull();
  });

  it("omits the CTA when none is given", () => {
    render(<EmptyState icon={InboxIcon} title="Empty" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
