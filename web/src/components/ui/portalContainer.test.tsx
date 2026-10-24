import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PortalContainerContext } from "@/lib/portalContainer";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";

/** R13a risk resolution 2: Popover/Sheet content must be able to portal into
 * an arbitrary element (the fullscreened viewer stage) instead of always
 * `document.body` -- the default `Portal` target renders outside a
 * fullscreened element and so becomes invisible while fullscreen is active.
 * These pin the `container` prop actually reaching Radix's `Portal`. */
describe("Popover/Sheet container prop", () => {
  it("renders PopoverContent inside a custom container when provided", () => {
    const container = document.createElement("div");
    container.setAttribute("data-testid", "custom-portal-target");
    document.body.appendChild(container);

    render(
      <Popover defaultOpen>
        <PopoverTrigger>Open</PopoverTrigger>
        <PopoverContent container={container}>Popover body</PopoverContent>
      </Popover>,
    );

    const content = screen.getByText("Popover body");
    expect(container.contains(content)).toBe(true);

    document.body.removeChild(container);
  });

  it("renders SheetContent inside a custom container when provided", () => {
    const container = document.createElement("div");
    container.setAttribute("data-testid", "custom-sheet-target");
    document.body.appendChild(container);

    render(
      <Sheet defaultOpen>
        <SheetTrigger>Open</SheetTrigger>
        <SheetContent container={container}>Sheet body</SheetContent>
      </Sheet>,
    );

    const content = screen.getByText("Sheet body");
    expect(container.contains(content)).toBe(true);

    document.body.removeChild(container);
  });
});

describe("PortalContainerContext", () => {
  it("portals overlays into the panel container unless one is passed explicitly", () => {
    const panel = document.createElement("div");
    document.body.appendChild(panel);

    render(
      <PortalContainerContext.Provider value={panel}>
        <Popover defaultOpen>
          <PopoverTrigger>Open</PopoverTrigger>
          <PopoverContent>Ctx body</PopoverContent>
        </Popover>
      </PortalContainerContext.Provider>,
    );

    expect(panel.contains(screen.getByText("Ctx body"))).toBe(true);
    document.body.removeChild(panel);
  });
});
