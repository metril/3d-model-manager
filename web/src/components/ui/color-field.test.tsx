import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ColorField } from "@/components/ui/color-field";

function setup(value = "") {
  const onChange = vi.fn();
  render(<ColorField label="Colour" value={value} onChange={onChange} swatches={["#ef4444"]} />);
  fireEvent.click(screen.getByLabelText("Colour"));
  return onChange;
}

describe("ColorField", () => {
  it("shows the current colour on the trigger", () => {
    render(<ColorField label="Colour" value="#AABBCC" onChange={vi.fn()} />);
    expect(screen.getByLabelText("Colour")).toHaveTextContent("#aabbcc");
  });

  it("typing a valid hex updates the value", () => {
    const onChange = setup();
    fireEvent.change(screen.getByLabelText("Hex color"), { target: { value: "#12AB34" } });
    expect(onChange).toHaveBeenCalledWith("#12ab34");
  });

  it("ignores an invalid hex", () => {
    const onChange = setup();
    fireEvent.change(screen.getByLabelText("Hex color"), { target: { value: "#12zz" } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clicking a swatch sets the value", () => {
    const onChange = setup();
    fireEvent.click(screen.getByLabelText("Color #ef4444"));
    expect(onChange).toHaveBeenCalledWith("#ef4444");
  });

  it("clear empties an optional colour", () => {
    const onChange = setup("#ef4444");
    fireEvent.click(screen.getByLabelText("Clear colour"));
    expect(onChange).toHaveBeenCalledWith("");
  });
});
