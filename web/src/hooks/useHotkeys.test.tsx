import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useHotkeys } from "@/hooks/useHotkeys";

function fireKey(init: KeyboardEventInit, target: EventTarget = document.body) {
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

function Bound({
  bindings,
  enabled,
}: {
  bindings: Record<string, (event: KeyboardEvent) => void>;
  enabled?: boolean;
}) {
  useHotkeys(bindings, { enabled });
  return (
    <div>
      <input aria-label="text-input" />
      <textarea aria-label="text-area" />
      <div aria-label="editable" contentEditable suppressContentEditableWarning />
    </div>
  );
}

describe("useHotkeys", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fires the handler for a plain key", () => {
    const handler = vi.fn();
    render(<Bound bindings={{ a: handler }} />);
    fireKey({ key: "a" });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("distinguishes case-sensitive letters (f vs F/shift+f)", () => {
    const lower = vi.fn();
    const upper = vi.fn();
    render(<Bound bindings={{ f: lower, F: upper }} />);

    fireKey({ key: "f" });
    expect(lower).toHaveBeenCalledTimes(1);
    expect(upper).not.toHaveBeenCalled();

    fireKey({ key: "F", shiftKey: true });
    expect(upper).toHaveBeenCalledTimes(1);
    expect(lower).toHaveBeenCalledTimes(1);
  });

  it("matches mod+a as Ctrl on non-Mac platforms", () => {
    vi.stubGlobal("navigator", { ...navigator, platform: "Linux x86_64" });
    const handler = vi.fn();
    render(<Bound bindings={{ "mod+a": handler }} />);

    fireKey({ key: "a", ctrlKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("matches mod+a as Meta on Mac", () => {
    vi.stubGlobal("navigator", { ...navigator, platform: "MacIntel" });
    const handler = vi.fn();
    render(<Bound bindings={{ "mod+a": handler }} />);

    fireKey({ key: "a", metaKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not fire a bare key binding when a modifier is held (e.g. mod+a vs a)", () => {
    const plain = vi.fn();
    const withMod = vi.fn();
    render(<Bound bindings={{ a: plain, "mod+a": withMod }} />);

    fireKey({ key: "a", ctrlKey: true });
    expect(withMod).toHaveBeenCalledTimes(1);
    expect(plain).not.toHaveBeenCalled();
  });

  it("ignores events targeting an input, textarea, or contenteditable", () => {
    const handler = vi.fn();
    const { getByLabelText } = render(<Bound bindings={{ a: handler }} />);

    fireKey({ key: "a" }, getByLabelText("text-input"));
    fireKey({ key: "a" }, getByLabelText("text-area"));
    fireKey({ key: "a" }, getByLabelText("editable"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("fires a mod+key binding even when the target is an input (e.g. mod+k, mod+b)", () => {
    const paletteHandler = vi.fn();
    const sidebarHandler = vi.fn();
    const { getByLabelText } = render(
      <Bound bindings={{ "mod+k": paletteHandler, "mod+b": sidebarHandler }} />,
    );

    fireKey({ key: "k", ctrlKey: true }, getByLabelText("text-input"));
    fireKey({ key: "b", ctrlKey: true }, getByLabelText("text-input"));
    expect(paletteHandler).toHaveBeenCalledTimes(1);
    expect(sidebarHandler).toHaveBeenCalledTimes(1);
  });

  it("still ignores a plain-key binding on an editable target even when another binding for the same key carries a modifier", () => {
    const plain = vi.fn();
    const withMod = vi.fn();
    const { getByLabelText } = render(<Bound bindings={{ k: plain, "mod+k": withMod }} />);

    fireKey({ key: "k" }, getByLabelText("text-input"));
    expect(plain).not.toHaveBeenCalled();
    expect(withMod).not.toHaveBeenCalled();
  });

  it("still fires Escape even when the target is an input", () => {
    const handler = vi.fn();
    const { getByLabelText } = render(<Bound bindings={{ Escape: handler }} />);

    fireKey({ key: "Escape" }, getByLabelText("text-input"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("ignores repeated keydown events", () => {
    const handler = vi.fn();
    render(<Bound bindings={{ a: handler }} />);

    fireKey({ key: "a", repeat: true });
    expect(handler).not.toHaveBeenCalled();
  });

  it("calls preventDefault only when a binding matches", () => {
    render(<Bound bindings={{ a: vi.fn() }} />);

    const matched = fireKey({ key: "a" });
    expect(matched.defaultPrevented).toBe(true);

    const unmatched = fireKey({ key: "z" });
    expect(unmatched.defaultPrevented).toBe(false);
  });

  it("reads the latest bindings without requiring the caller to memoize them", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Bound bindings={{ a: first }} />);
    rerender(<Bound bindings={{ a: second }} />);

    fireKey({ key: "a" });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("does not attach a listener when disabled", () => {
    const handler = vi.fn();
    render(<Bound bindings={{ a: handler }} enabled={false} />);

    fireKey({ key: "a" });
    expect(handler).not.toHaveBeenCalled();
  });

  it("removes its listener on unmount", () => {
    const handler = vi.fn();
    const { unmount } = render(<Bound bindings={{ a: handler }} />);
    unmount();

    fireKey({ key: "a" });
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("useHotkeys guards", () => {
  it("mod+a does not fire inside a text input but mod+k still does", () => {
    const a = vi.fn();
    const k = vi.fn();
    const { getByLabelText } = render(<Bound bindings={{ "ctrl+a": a, "ctrl+k": k }} />);
    const input = getByLabelText("text-input");
    fireKey({ key: "a", ctrlKey: true }, input);
    fireKey({ key: "k", ctrlKey: true }, input);
    expect(a).not.toHaveBeenCalled();
    expect(k).toHaveBeenCalledTimes(1);
  });

  it("bare keys are skipped inside a menu and while a dialog is open", () => {
    const f = vi.fn();
    render(<Bound bindings={{ f }} />);
    const menu = document.createElement("div");
    menu.setAttribute("role", "menu");
    const item = document.createElement("button");
    menu.appendChild(item);
    document.body.appendChild(menu);
    fireKey({ key: "f" }, item);
    expect(f).not.toHaveBeenCalled();
    menu.remove();

    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("data-state", "open");
    document.body.appendChild(dialog);
    fireKey({ key: "f" });
    expect(f).not.toHaveBeenCalled();
    dialog.remove();

    fireKey({ key: "f" });
    expect(f).toHaveBeenCalledTimes(1);
  });
});
