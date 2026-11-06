import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as THREE from "three";

import type { FileOut } from "@/api/types";
import { NumField, PlateEditorDialog } from "@/components/plate/PlateEditorDialog";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, get: getMock, post: postMock } };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn(), dismiss: vi.fn() } }));
vi.mock("@/components/plate/PlateScene", () => ({ default: () => <div data-testid="plate-scene" /> }));
vi.mock("@/components/slice/SliceDialog", () => ({ SliceDialog: () => null }));

const FILE = {
  id: 1,
  rel_path: "multi.3mf",
  format: "3mf",
  blob_hash: "h1",
  verified_at: "2026-01-01T00:00:00Z",
  glb_status: "ok",
} as unknown as FileOut;

const obj = (index: number, name: string, x: number) => ({
  index, plate: 1, name, x_mm: x, y_mm: 20, w_mm: 10, d_mm: 10, h_mm: 10, glb_url: `/o${index}.glb`,
});

beforeEach(() => {
  localStorage.clear();
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/profiles")) return Promise.resolve({ items: [], total: 0 });
    if (path.endsWith("/objects")) return Promise.resolve({ file_id: 1, objects: [obj(0, "Obj A", 20), obj(1, "Obj B", 120)] });
    return Promise.resolve({});
  });
});

describe("PlateEditorDialog", () => {
  it("Duplicate keeps the object_index so the copy stays that object", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <PlateEditorDialog open onOpenChange={() => {}} slug="multi" files={[FILE]} />
      </QueryClientProvider>,
    );
    const dups = await screen.findAllByRole("button", { name: "Duplicate" });
    expect(screen.getAllByTestId("plate-item")).toHaveLength(2);
    fireEvent.click(dups[0]!);
    // The copy renders under its own object's group (it would vanish with a null object_index).
    await waitFor(() => expect(screen.getAllByTestId("plate-item")).toHaveLength(3));
    expect(screen.getByText("#2")).toBeInTheDocument();
  });
});

describe("NumField", () => {
  function Harness() {
    const [v, setV] = useState(5);
    return <NumField label="X" value={v} onFocus={() => {}} onChange={setV} />;
  }

  it("keeps partial input in a local draft and re-syncs on blur", () => {
    render(<Harness />);
    const input = screen.getByLabelText("X") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "12" } });
    expect(input.value).toBe("12");
    // Cleared field is not forced back to the numeric value mid-edit.
    fireEvent.change(input, { target: { value: "" } });
    expect(input.value).toBe("");
    fireEvent.blur(input);
    expect(input.value).toBe("12");
  });
});

describe("PlateScene", () => {
  it("never touches the global Object3D.DEFAULT_UP", async () => {
    vi.resetModules();
    vi.doMock("@react-three/fiber", () => ({ Canvas: () => null, useThree: () => () => {} }));
    vi.doMock("@react-three/drei", () => ({ OrbitControls: () => null, TransformControls: () => null, useGLTF: Object.assign(() => ({}), { clear: () => {} }) }));
    const before = THREE.Object3D.DEFAULT_UP.toArray();
    const { default: PlateScene } = await import("@/components/plate/PlateScene");
    const { unmount } = render(
      <PlateScene
        bed={{ x_mm: 256, y_mm: 256, height: 256 }}
        items={[]}
        parts={new Map()}
        selected={null}
        fitsByUid={new Map()}
        mode="translate"
        freeRotate={false}
        orbitLocked={false}
        faceMode={false}
        onSelect={() => {}}
        onDims={() => {}}
        onFace={() => {}}
        onBegin={() => {}}
        onChange={() => {}}
        onOrbitLock={() => {}}
      />,
    );
    expect(THREE.Object3D.DEFAULT_UP.toArray()).toEqual(before);
    unmount();
    expect(THREE.Object3D.DEFAULT_UP.toArray()).toEqual([0, 1, 0]);
    vi.doUnmock("@react-three/fiber");
    vi.doUnmock("@react-three/drei");
  });
});
