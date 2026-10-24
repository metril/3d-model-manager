/** Schedules R3F frames on the `requestAnimationFrame` of whichever window
 * currently hosts the canvas. R3F's own loop is bound to the window it was
 * created in; a canvas docked into a pop-out window would otherwise be driven
 * by the (possibly hidden/minimised, hence throttled) main window. */
export type RafWindow = Pick<Window, "requestAnimationFrame" | "cancelAnimationFrame">;

export function createRafScheduler(getWindow: () => RafWindow, advance: (seconds: number) => void) {
  let handle = 0;
  let scheduledOn: RafWindow | null = null;

  const cancel = () => {
    if (handle && scheduledOn) scheduledOn.cancelAnimationFrame(handle);
    handle = 0;
    scheduledOn = null;
  };

  /** Request a frame. Re-targets (cancel + reschedule) if the host window changed. */
  const invalidate = () => {
    const win = getWindow();
    if (handle && scheduledOn === win) return; // already queued on the right window
    cancel();
    scheduledOn = win;
    handle = win.requestAnimationFrame((timestamp) => {
      handle = 0;
      scheduledOn = null;
      advance(timestamp / 1000);
    });
  };

  return { invalidate, cancel };
}
