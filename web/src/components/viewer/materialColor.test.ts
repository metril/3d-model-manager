import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { applyColorOverride } from "@/components/viewer/materialColor";

function pair() {
  const source = new THREE.MeshStandardMaterial({ color: "#ffffff", vertexColors: true });
  const owned = source.clone();
  return { source, owned };
}

describe("applyColorOverride", () => {
  it("keeps vertex (filament) colours by default", () => {
    const { source, owned } = pair();
    applyColorOverride(owned, source, undefined);
    expect(owned.vertexColors).toBe(true);
    expect(owned.color.getHexString()).toBe("ffffff");
  });

  it("a swatch overrides to a uniform colour (vertex colours off)", () => {
    const { source, owned } = pair();
    applyColorOverride(owned, source, "#ff8a00");
    expect(owned.vertexColors).toBe(false);
    expect(owned.color.getHexString()).toBe("ff8a00");
  });

  it("clearing the swatch restores the original vertex colours", () => {
    const { source, owned } = pair();
    applyColorOverride(owned, source, "#ff8a00");
    applyColorOverride(owned, source, undefined);
    expect(owned.vertexColors).toBe(true);
    expect(owned.color.getHexString()).toBe("ffffff");
  });

  it("leaves plain materials alone", () => {
    const source = new THREE.MeshStandardMaterial({ color: "#123456" });
    const owned = source.clone();
    applyColorOverride(owned, source, undefined);
    expect(owned.vertexColors).toBe(false);
    expect(owned.color.getHexString()).toBe("123456");
  });
});
