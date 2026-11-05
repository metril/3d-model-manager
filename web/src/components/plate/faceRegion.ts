import type * as THREE from "three";

/** Above this many triangles only the hovered one is highlighted (the scan runs per hover). */
export const MAX_REGION_SCAN = 150_000;

const NORMAL_COS = 0.9999;
const PLANE_TOL_MM = 0.05;

/**
 * Triangle indices coplanar with `faceIndex` (same normal, same plane offset)
 * in `geometry`'s local space: the flat region that would rest on the bed.
 */
export function coplanarTriangles(geometry: THREE.BufferGeometry, faceIndex: number): number[] {
  const pos = geometry.getAttribute("position");
  const index = geometry.getIndex();
  const count = index ? index.count / 3 : pos.count / 3;
  if (faceIndex < 0 || faceIndex >= count) return [];
  if (count > MAX_REGION_SCAN) return [faceIndex];

  const vert = (tri: number, k: number) => (index ? index.getX(tri * 3 + k) : tri * 3 + k);
  const out = new Float64Array(9);
  const plane = (tri: number): [number, number, number, number] | null => {
    for (let k = 0; k < 3; k++) {
      const v = vert(tri, k);
      out[k * 3] = pos.getX(v);
      out[k * 3 + 1] = pos.getY(v);
      out[k * 3 + 2] = pos.getZ(v);
    }
    const ux = out[3] - out[0];
    const uy = out[4] - out[1];
    const uz = out[5] - out[2];
    const wx = out[6] - out[0];
    const wy = out[7] - out[1];
    const wz = out[8] - out[2];
    let nx = uy * wz - uz * wy;
    let ny = uz * wx - ux * wz;
    let nz = ux * wy - uy * wx;
    const len = Math.hypot(nx, ny, nz);
    if (len < 1e-12) return null;
    nx /= len;
    ny /= len;
    nz /= len;
    return [nx, ny, nz, nx * out[0] + ny * out[1] + nz * out[2]];
  };

  const ref = plane(faceIndex);
  if (!ref) return [faceIndex];
  const hits: number[] = [];
  for (let tri = 0; tri < count; tri++) {
    const p = plane(tri);
    if (!p) continue;
    if (p[0] * ref[0] + p[1] * ref[1] + p[2] * ref[2] >= NORMAL_COS && Math.abs(p[3] - ref[3]) <= PLANE_TOL_MM) hits.push(tri);
  }
  return hits.length ? hits : [faceIndex];
}

/** Non-indexed position buffer of the given triangles, for the hover overlay. */
export function trianglePositions(geometry: THREE.BufferGeometry, tris: number[]): Float32Array {
  const pos = geometry.getAttribute("position");
  const index = geometry.getIndex();
  const arr = new Float32Array(tris.length * 9);
  tris.forEach((tri, i) => {
    for (let k = 0; k < 3; k++) {
      const v = index ? index.getX(tri * 3 + k) : tri * 3 + k;
      arr[i * 9 + k * 3] = pos.getX(v);
      arr[i * 9 + k * 3 + 1] = pos.getY(v);
      arr[i * 9 + k * 3 + 2] = pos.getZ(v);
    }
  });
  return arr;
}
