import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/ConfirmDialog";

describe("ConfirmDialog", () => {
  it("does not call onCancel after Confirm, only after a real dismiss", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <ConfirmDialog open onOpenChange={() => {}} title="Sure?" onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    rerender(<ConfirmDialog open={false} onOpenChange={() => {}} title="Sure?" onConfirm={onConfirm} onCancel={onCancel} />);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("with `pending` set, Confirm keeps the dialog open and shows a spinner", () => {
    const onOpenChange = vi.fn();
    const { container, rerender } = render(
      <ConfirmDialog open onOpenChange={onOpenChange} title="Sure?" pending={false} onConfirm={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onOpenChange).not.toHaveBeenCalled();
    rerender(<ConfirmDialog open onOpenChange={onOpenChange} title="Sure?" pending onConfirm={() => {}} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
    expect(document.body.querySelector(".animate-spin")).not.toBeNull();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(container).toBeTruthy();
  });
});
