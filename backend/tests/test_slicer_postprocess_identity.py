"""``scripts/slicer_postprocess.py``, ``web/public/slicer_postprocess.py`` and
the legacy-URL copy ``web/public/bambu_postprocess.py`` are deliberately
copies of the exact same file -- the ``web/public`` ones are what the
frontend serves as the downloadable post-processing script (the old
``bambu_postprocess.py`` URL keeps working), and they can't simply import the
``scripts/`` one across the ``backend``/``web`` boundary. This is the durable
drift guard: fails loudly the moment they diverge.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COPIES = (
    "scripts/slicer_postprocess.py",
    "web/public/slicer_postprocess.py",
    "web/public/bambu_postprocess.py",
)


def test_postprocess_script_is_byte_identical_in_all_locations() -> None:
    paths = [REPO_ROOT / c for c in COPIES]
    for path in paths:
        assert path.is_file(), path
    assert len({p.read_bytes() for p in paths}) == 1, (
        f"{COPIES} have drifted -- keep them byte-identical (copy one over the others)."
    )
