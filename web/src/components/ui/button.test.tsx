import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/button";

describe("Button disabled", () => {
  it("keeps a native disabled button non-clickable", () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("makes a disabled asChild link inert", () => {
    render(
      <Button asChild disabled>
        <a href="/x">Link</a>
      </Button>,
    );
    const a = screen.getByText("Link");
    expect(a).toHaveAttribute("aria-disabled", "true");
    expect(a).toHaveAttribute("tabindex", "-1");
    expect(a.className).toContain("aria-disabled:pointer-events-none");
  });
});
