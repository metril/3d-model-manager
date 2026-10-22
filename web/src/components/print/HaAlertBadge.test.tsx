import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HaAlertBadge } from "@/components/print/HaAlertBadge";

describe("HaAlertBadge", () => {
  it("labels 'Held' and includes the reason in the tooltip/aria-label when ha_held", () => {
    render(<HaAlertBadge reason="Smoke detected" held />);
    const badge = screen.getByLabelText(/Smoke detected/);
    expect(badge).toHaveTextContent("Held");
    expect(badge).toHaveAttribute("title", "Smoke detected");
  });

  it("labels 'Alert' and includes the reason when not held", () => {
    render(<HaAlertBadge reason="Door open" held={false} />);
    const badge = screen.getByLabelText(/Door open/);
    expect(badge).toHaveTextContent("Alert");
  });
});
