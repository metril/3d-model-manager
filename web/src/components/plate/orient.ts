/**
 * Orientation maths for the plate editor. An item's `orient` is a unit
 * quaternion [x, y, z, w] applied to the source mesh before it is dropped on
 * the bed; the pose (Z rotation, scale, x/y) comes after. Mirrors
 * `backend/app/services/orient.py`.
 */
export type Quat = [number, number, number, number];
export type Vec3 = [number, number, number];

export const IDENTITY_QUAT: Quat = [0, 0, 0, 1];

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;

/** Unit quaternion with w >= 0; anything unusable becomes identity. */
export function normalizeQuat(q: readonly number[] | null | undefined): Quat {
  if (!q || q.length !== 4 || !q.every(Number.isFinite)) return [...IDENTITY_QUAT];
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  if (n < 1e-9) return [...IDENTITY_QUAT];
  const s = q[3] < 0 ? -1 / n : 1 / n;
  return [round6(q[0] * s) + 0, round6(q[1] * s) + 0, round6(q[2] * s) + 0, round6(q[3] * s) + 0];
}

export const isIdentityQuat = (q: readonly number[]) => {
  const n = normalizeQuat(q);
  return n[0] === 0 && n[1] === 0 && n[2] === 0 && n[3] === 1;
};

/** Hamilton product a * b: apply b first, then a. */
export function mulQuat(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

export function rotateVec(q: Quat, v: Vec3): Vec3 {
  const [x, y, z, w] = normalizeQuat(q);
  // v' = v + 2w(u x v) + 2 u x (u x v), u = (x, y, z)
  const cx = y * v[2] - z * v[1];
  const cy = z * v[0] - x * v[2];
  const cz = x * v[1] - y * v[0];
  return [
    v[0] + 2 * (w * cx + y * cz - z * cy),
    v[1] + 2 * (w * cy + z * cx - x * cz),
    v[2] + 2 * (w * cz + x * cy - y * cx),
  ];
}

/** Shortest rotation taking unit vector `a` onto unit vector `b`. */
export function quatBetween(a: Vec3, b: Vec3): Quat {
  const dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  if (dot > 1 - 1e-12) return [...IDENTITY_QUAT];
  if (dot < -1 + 1e-12) {
    // Opposite: half turn about any axis perpendicular to `a`.
    let axis: Vec3 = [0, -a[2], a[1]]; // a x (1,0,0)
    if (Math.hypot(...axis) < 1e-6) axis = [a[2], 0, -a[0]]; // a x (0,1,0)
    const n = Math.hypot(...axis);
    return [axis[0] / n, axis[1] / n, axis[2] / n, 0];
  }
  const c: Vec3 = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  return normalizeQuat([c[0], c[1], c[2], 1 + dot]);
}

const DOWN: Vec3 = [0, 0, -1];

/**
 * New orientation that lays a clicked face flat. `worldNormal` is the face's
 * outward normal in the scene (so it already includes the item's Z rotation
 * and orient). The item's heading (`rot_z_deg`) is kept: undo the Z rotation
 * to get the normal in the oriented-source frame, take the shortest tilt that
 * sends it to -Z, and compose that onto the current orient. Z rotation leaves
 * -Z fixed, so the face ends up flat on the bed whatever the heading.
 */
export function layOnFaceOrient(orient: readonly number[], rotZDeg: number, worldNormal: Vec3): Quat {
  const len = Math.hypot(...worldNormal);
  if (!(len > 1e-9)) return normalizeQuat(orient);
  const n: Vec3 = [worldNormal[0] / len, worldNormal[1] / len, worldNormal[2] / len];
  const half = (-rotZDeg * Math.PI) / 360;
  const unspun = rotateVec([0, 0, Math.sin(half), Math.cos(half)], n);
  return normalizeQuat(mulQuat(quatBetween(unspun, DOWN), normalizeQuat(orient)));
}
