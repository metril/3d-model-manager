import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useElementFullscreen } from "@/hooks/useElementFullscreen";

function Probe() {
  const ref = useRef<HTMLDivElement>(null);
  const fs = useElementFullscreen(ref);
  return (
    <div ref={ref} data-testid="box">
      <button onClick={fs.toggle}>toggle</button>
      <span>{fs.active ? "on" : "off"}</span>
    </div>
  );
}

afterEach(() => {
  Reflect.deleteProperty(document, "fullscreenElement");
});

describe("useElementFullscreen", () => {
  it("requests fullscreen on the element and tracks fullscreenchange", () => {
    const request = vi.fn().mockResolvedValue(undefined);
    document.documentElement.requestFullscreen = request;
    render(<Probe />);
    const box = screen.getByTestId("box");
    box.requestFullscreen = request;

    fireEvent.click(screen.getByText("toggle"));
    expect(request).toHaveBeenCalled();

    Object.defineProperty(document, "fullscreenElement", { value: box, configurable: true });
    act(() => {
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    expect(screen.getByText("on")).toBeInTheDocument();

    const exit = vi.fn().mockResolvedValue(undefined);
    document.exitFullscreen = exit;
    fireEvent.click(screen.getByText("toggle"));
    expect(exit).toHaveBeenCalled();
  });
});
