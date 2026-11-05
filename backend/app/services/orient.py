"""Best print orientation of a mesh, and the quaternion maths shared with the
plate editor (``mesh_combine``).

Candidates are the convex hull's facets (coplanar hull faces merged), biggest
first; each candidate lays that facet on the bed. A candidate scores the
fraction of surface area resting on the bed minus the fraction that is a
shallow overhang (downward-facing within 45 degrees of straight down, and not
on the bed), with a light penalty for height. Pure numpy and deterministic.
"""

from __future__ import annotations

import math

import numpy as np

Quat = tuple[float, float, float, float]  # x, y, z, w
IDENTITY: Quat = (0.0, 0.0, 0.0, 1.0)

MAX_CANDIDATES = 24
CONTACT_TOL_MM = 0.2
FLAT_COS = math.cos(math.radians(2.0))
OVERHANG_NZ = -math.sin(math.radians(45.0))
HEIGHT_WEIGHT = 0.1
IDENTITY_BONUS = 0.002  # keep the current pose when nothing is clearly better
_MAX_HULL_POINTS = 200_000
_MAX_SCORE_FACES = 300_000


def normalize(q: Quat) -> Quat:
    """Unit quaternion with ``w >= 0`` (q and -q are the same rotation)."""
    n = math.sqrt(sum(c * c for c in q))
    if not math.isfinite(n) or n < 1e-9:
        return IDENTITY
    x, y, z, w = (c / n for c in q)
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    return (x, y, z, w)


def quat_key(q: Quat) -> tuple[int, ...]:
    """Hashable key so equal orientations (to ~1e-5) share one loaded copy."""
    return tuple(int(round(c * 1e5)) + 0 for c in normalize(q))


def is_identity(q: Quat) -> bool:
    return all(abs(a - b) < 1e-9 for a, b in zip(normalize(q), IDENTITY, strict=True))


def quat_matrix(q: Quat) -> np.ndarray:
    """3x3 rotation matrix ``R`` (column vectors: ``v' = R @ v``)."""
    x, y, z, w = normalize(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def rotate_vertices(vertices: np.ndarray, q: Quat) -> np.ndarray:
    if is_identity(q):
        return vertices
    return vertices @ quat_matrix(q).T


def quat_between(a: np.ndarray, b: np.ndarray) -> Quat:
    """Shortest rotation taking unit vector ``a`` onto unit vector ``b``."""
    dot = float(np.dot(a, b))
    if dot > 1 - 1e-12:
        return IDENTITY
    if dot < -1 + 1e-12:
        axis = np.cross(a, [1.0, 0.0, 0.0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return (float(axis[0]), float(axis[1]), float(axis[2]), 0.0)
    axis = np.cross(a, b)
    return normalize((float(axis[0]), float(axis[1]), float(axis[2]), 1.0 + dot))


def _candidates(vertices: np.ndarray, faces: np.ndarray) -> list[np.ndarray]:
    """Outward normals of the biggest convex-hull facets (merged by normal)."""
    import trimesh

    pts = np.unique(vertices[np.unique(faces)], axis=0) if len(faces) < 50_000 else vertices
    if len(pts) > _MAX_HULL_POINTS:
        pts = pts[:: math.ceil(len(pts) / _MAX_HULL_POINTS)]
    try:
        hull = trimesh.convex.convex_hull(pts)
    except Exception:
        return []
    if len(hull.faces) == 0:
        return []
    groups: dict[tuple[int, ...], list] = {}
    for n, a in zip(hull.face_normals, hull.area_faces, strict=True):
        slot = groups.setdefault(tuple(np.round(n * 500).astype(int)), [np.zeros(3), 0.0])
        slot[0] += n * a
        slot[1] += a
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1][1], kv[0]))[:MAX_CANDIDATES]
    out = []
    for _, (nsum, _area) in ranked:
        norm = np.linalg.norm(nsum)
        if norm > 1e-12:
            out.append(nsum / norm)
    return out


def best_orientation(vertices: np.ndarray, faces: np.ndarray) -> Quat:
    """Quaternion to apply to ``vertices`` for the best print orientation."""
    verts = np.asarray(vertices, dtype=float)
    tris = np.asarray(faces, dtype=np.int64)
    if len(tris) == 0 or len(verts) == 0:
        return IDENTITY
    step = max(1, math.ceil(len(tris) / _MAX_SCORE_FACES))
    tris = tris[::step]
    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    cross = np.cross(p1 - p0, p2 - p0)
    area = np.linalg.norm(cross, axis=1) / 2
    ok = area > 1e-12
    if not ok.any():
        return IDENTITY
    p0, p1, p2, cross, area = p0[ok], p1[ok], p2[ok], cross[ok], area[ok]
    normals = cross / (2 * area[:, None])
    total = float(area.sum())
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    diag = float(np.linalg.norm(hi - lo)) or 1.0

    best_q, best_score = IDENTITY, -math.inf
    down = np.array([0.0, 0.0, -1.0])
    cands: list[tuple[Quat, float]] = [(IDENTITY, IDENTITY_BONUS)]
    cands += [(quat_between(n, down), 0.0) for n in _candidates(verts, faces)]
    for q, bonus in cands:
        rot = quat_matrix(q)
        nz = normals @ rot[2]
        z0, z1, z2 = p0 @ rot[2], p1 @ rot[2], p2 @ rot[2]
        zmin = min(float(z0.min()), float(z1.min()), float(z2.min()))
        zmax = max(float(z0.max()), float(z1.max()), float(z2.max()))
        on_bed = (nz < -FLAT_COS) & (np.maximum(np.maximum(z0, z1), z2) - zmin < CONTACT_TOL_MM)
        overhang = (nz < OVERHANG_NZ) & ~on_bed
        score = (
            float(area[on_bed].sum()) / total
            - float(area[overhang].sum()) / total
            - HEIGHT_WEIGHT * (zmax - zmin) / diag
            + bonus
        )
        if score > best_score + 1e-9:
            best_q, best_score = q, score
    return normalize(best_q)
