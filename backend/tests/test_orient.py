from __future__ import annotations

import numpy as np
import pytest
import trimesh

from app.services.orient import (
    IDENTITY,
    best_orientation,
    normalize,
    quat_between,
    quat_matrix,
    rotate_vertices,
)

SIDE = (0.7071067811865476, 0.0, 0.0, 0.7071067811865476)


def _oriented(mesh: trimesh.Trimesh):
    q = best_orientation(mesh.vertices, mesh.faces)
    v = rotate_vertices(np.asarray(mesh.vertices), q)
    return q, v.max(axis=0) - v.min(axis=0)


def _spun(mesh: trimesh.Trimesh, angle: float, axis) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, axis))
    return mesh


def test_quat_between_maps_vector_onto_target() -> None:
    rng = np.random.default_rng(1)
    down = np.array([0.0, 0.0, -1.0])
    for _ in range(20):
        n = rng.normal(size=3)
        n /= np.linalg.norm(n)
        assert quat_matrix(quat_between(n, down)) @ n == pytest.approx(down, abs=1e-9)
    assert quat_between(down, down) == IDENTITY
    up = quat_between(np.array([0.0, 0.0, 1.0]), down)
    assert quat_matrix(up) @ [0, 0, 1] == pytest.approx(down, abs=1e-9)
    assert normalize((0, 0, 0, -1)) == (0.0, 0.0, 0.0, 1.0)


def test_box_on_its_side_goes_flat() -> None:
    box = trimesh.creation.box(extents=(10, 10, 40))
    _, size = _oriented(box)
    assert size == pytest.approx([40, 10, 10]) or sorted(size) == pytest.approx([10, 10, 40])
    assert size[2] == pytest.approx(10)


def test_flat_box_is_left_alone() -> None:
    q, size = _oriented(trimesh.creation.box(extents=(30, 20, 5)))
    assert q == IDENTITY and size[2] == pytest.approx(5)


def test_l_bracket_lies_on_its_big_base_whatever_the_start() -> None:
    base = trimesh.creation.box(
        extents=(40, 30, 4), transform=trimesh.transformations.translation_matrix((0, 0, 2))
    )
    wall = trimesh.creation.box(
        extents=(40, 4, 16), transform=trimesh.transformations.translation_matrix((0, -13, 12))
    )
    bracket = trimesh.util.concatenate([base, wall])
    for angle, axis in [
        (0, [1, 0, 0]),
        (np.pi / 2, [1, 0, 0]),
        (np.pi, [0, 1, 0]),
        (1.0, [1, 1, 0]),
    ]:
        _, size = _oriented(_spun(bracket, angle, axis))
        assert size[2] == pytest.approx(20), (angle, size)


def test_cylinder_stands_rather_than_lying() -> None:
    cyl = trimesh.creation.cylinder(radius=5, height=40, sections=64)
    for start in (cyl, _spun(cyl, np.pi / 2, [1, 0, 0]), _spun(cyl, 0.7, [1, 1, 0])):
        _, size = _oriented(start)
        assert size[2] == pytest.approx(40, abs=0.2)


def test_deterministic() -> None:
    mesh = _spun(trimesh.creation.icosphere(subdivisions=2), 0.4, [0, 1, 1])
    assert best_orientation(mesh.vertices, mesh.faces) == best_orientation(
        mesh.vertices, mesh.faces
    )
