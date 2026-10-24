import { act, render } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { useStageFullscreen } from "@/components/viewer/useStageFullscreen";
import { RelocateContext, createRelocateEmitter } from "@/lib/windowRelocate";

function Harness({ expose }: { expose: (api: ReturnType<typeof useStageFullscreen>) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  expose(useStageFullscreen(ref, true));
  return <div ref={ref} data-testid="stage" />;
}

describe("useStageFullscreen", () => {
  it("exits fullscreen on the stage's own ownerDocument, not the main document", () => {
    const popoutDoc = document.implementation.createHTMLDocument("popout");
    const exit = vi.fn();
    Object.defineProperty(popoutDoc, "fullscreenElement", { configurable: true, value: popoutDoc.body });
    Object.defineProperty(popoutDoc, "exitFullscreen", { configurable: true, value: exit });
    const mainExit = vi.fn();
    Object.defineProperty(document, "exitFullscreen", { configurable: true, value: mainExit });

    let api!: ReturnType<typeof useStageFullscreen>;
    const { getByTestId } = render(<Harness expose={(value) => (api = value)} />);
    // Simulate the dock moving the stage into the popout window's document.
    const stage = getByTestId("stage");
    const home = stage.parentElement!;
    popoutDoc.body.appendChild(stage);

    api.toggleFullscreen();

    expect(exit).toHaveBeenCalledOnce();
    expect(mainExit).not.toHaveBeenCalled();
    home.appendChild(stage); // let React unmount cleanly
  });

  it("binds Shift+F to the hosting document after a relocate", () => {
    const popoutDoc = document.implementation.createHTMLDocument("popout");
    const emitter = createRelocateEmitter();
    const request = vi.fn();
    const { getByTestId } = render(
      <RelocateContext.Provider value={emitter}>
        <Harness expose={() => {}} />
      </RelocateContext.Provider>,
    );
    const stage = getByTestId("stage");
    stage.requestFullscreen = request as never;
    const home = stage.parentElement!;
    popoutDoc.body.appendChild(stage);

    act(() => emitter.emit());
    popoutDoc.dispatchEvent(new KeyboardEvent("keydown", { key: "F", shiftKey: true, bubbles: true }));

    expect(request).toHaveBeenCalledOnce();
    home.appendChild(stage);
  });
});
