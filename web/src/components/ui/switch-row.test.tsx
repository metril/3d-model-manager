import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SwitchRow } from "@/components/ui/switch-row";

describe("SwitchRow", () => {
  it("toggles when the label is clicked", () => {
    const onChange = vi.fn();
    render(<SwitchRow label="Hold queue" checked={false} onCheckedChange={onChange} />);
    fireEvent.click(screen.getByText("Hold queue"));
    expect(onChange).toHaveBeenCalledWith(true);
    expect(screen.getByRole("switch", { name: "Hold queue" })).toBeInTheDocument();
  });

  it("renders the description and indent class", () => {
    const { container } = render(
      <SwitchRow label="A" description="Because" indent checked onCheckedChange={() => {}} />,
    );
    expect(screen.getByText("Because")).toBeInTheDocument();
    expect(container.firstElementChild?.className).toContain("pl-(--switch-indent)");
  });

  it("does not toggle when disabled", () => {
    const onChange = vi.fn();
    render(<SwitchRow label="Off" disabled checked={false} onCheckedChange={onChange} />);
    fireEvent.click(screen.getByText("Off"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
