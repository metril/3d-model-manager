import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FolderTree } from "@/components/model-detail/explorer/FolderTree";
import { buildTree } from "@/components/model-detail/explorer/tree";
import type { FileOut } from "@/api/types";

const f = (rel_path: string, id: number) => ({ id, rel_path }) as unknown as FileOut;
const root = buildTree([f("a.stl", 1), f("parts/b.stl", 2), f("parts/inner/c.stl", 3), f("pics/d.png", 4)]);

function setup(current = "") {
  const onSelect = vi.fn();
  render(<FolderTree root={root} current={current} onSelect={onSelect} rootLabel="Files" />);
  const item = (path: string) => document.querySelector<HTMLElement>(`[data-tree-path="${path}"]`)!;
  return { onSelect, item };
}

describe("FolderTree keyboard", () => {
  it("has a single roving tab stop on the current folder", () => {
    const { item } = setup("parts");
    expect(item("parts")).toHaveAttribute("tabindex", "0");
    expect(item("")).toHaveAttribute("tabindex", "-1");
    expect(item("pics")).toHaveAttribute("tabindex", "-1");
  });

  it("moves with Up/Down, Home and End", () => {
    const { item } = setup();
    item("").focus();
    fireEvent.keyDown(item(""), { key: "ArrowDown" });
    expect(item("parts")).toHaveFocus();
    fireEvent.keyDown(item("parts"), { key: "ArrowDown" });
    expect(item("pics")).toHaveFocus();
    fireEvent.keyDown(item("pics"), { key: "ArrowUp" });
    expect(item("parts")).toHaveFocus();
    fireEvent.keyDown(item("parts"), { key: "End" });
    expect(item("pics")).toHaveFocus();
    fireEvent.keyDown(item("pics"), { key: "Home" });
    expect(item("")).toHaveFocus();
    expect(item("pics")).toHaveAttribute("tabindex", "-1");
  });

  it("expands with Right, steps into the first child, collapses with Left, then steps out", () => {
    const { item } = setup();
    expect(item("parts/inner")).toBeNull();
    item("parts").focus();
    fireEvent.keyDown(item("parts"), { key: "ArrowRight" });
    expect(item("parts")).toHaveAttribute("aria-expanded", "true");
    expect(item("parts/inner")).not.toBeNull();
    fireEvent.keyDown(item("parts"), { key: "ArrowRight" });
    expect(item("parts/inner")).toHaveFocus();
    fireEvent.keyDown(item("parts/inner"), { key: "ArrowLeft" });
    expect(item("parts")).toHaveFocus();
    fireEvent.keyDown(item("parts"), { key: "ArrowLeft" });
    expect(item("parts")).toHaveAttribute("aria-expanded", "false");
    expect(item("parts/inner")).toBeNull();
  });

  it("opens the focused folder with Enter", () => {
    const { item, onSelect } = setup();
    item("pics").focus();
    fireEvent.keyDown(item("pics"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("pics");
  });

  it("lets the chevron toggle by mouse too", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Expand parts" }));
    expect(screen.getByRole("button", { name: "Collapse parts" })).toBeInTheDocument();
  });
});
