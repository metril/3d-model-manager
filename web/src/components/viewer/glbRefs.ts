import { useGLTF } from "@react-three/drei";
import type * as THREE from "three";

/** Refcounts mounted GLB parts per URL. When the LAST mount of a url goes away
 * (plate swap, profile switch, part removed) its cached glTF is evicted from
 * drei's cache and its geometry/textures are disposed, so cycling through many
 * plates doesn't grow GPU/CPU memory without bound. Release is deferred a tick
 * so a StrictMode remount (or a same-url swap) re-acquires first. */
const counts = new Map<string, number>();

export function disposeScene(scene: THREE.Object3D) {
  scene.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry?.dispose();
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of mats) {
      if (!m) continue;
      for (const v of Object.values(m)) if ((v as THREE.Texture | null)?.isTexture) (v as THREE.Texture).dispose();
    }
  });
}

export function acquireGlb(url: string) {
  counts.set(url, (counts.get(url) ?? 0) + 1);
}

export function releaseGlb(url: string, scene: THREE.Object3D, evict: (url: string) => void = (u) => useGLTF.clear(u)) {
  counts.set(url, (counts.get(url) ?? 1) - 1);
  setTimeout(() => {
    if ((counts.get(url) ?? 0) > 0) return;
    counts.delete(url);
    disposeScene(scene);
    evict(url);
  }, 0);
}
