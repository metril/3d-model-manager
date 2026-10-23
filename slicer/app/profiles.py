"""Index of Bambu Studio's bundled `resources/profiles/BBL` presets and
`inherits` flattening (same-kind, by name; cycle- and depth-guarded)."""

from __future__ import annotations

import json
from pathlib import Path

KINDS = ("printer", "process", "filament")
_DIR_BY_KIND = {"printer": "machine", "process": "process", "filament": "filament"}
MAX_DEPTH = 16


class ProfileError(ValueError):
    """Unknown parent, inherits cycle, or chain too deep."""


def _nozzle(value) -> float | None:
    try:
        return float(value[0] if isinstance(value, list) and value else value)
    except (TypeError, ValueError):
        return None


class ProfileIndex:
    def __init__(self, root: Path | None = None) -> None:
        self.by_kind: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        if root is None:
            return
        for kind in KINDS:
            folder = Path(root) / _DIR_BY_KIND[kind]
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    raw = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if isinstance(raw, dict):
                    self.by_kind[kind][str(raw.get("name", path.stem))] = raw

    def flatten_dict(self, kind: str, raw: dict) -> dict:
        """Resolve `raw`'s `inherits` chain against the bundled presets of
        `kind`; child keys win. Used for bundled and user-posted profiles."""
        pool = self.by_kind[kind]
        chain = [raw]
        seen = {str(raw.get("name", ""))}
        cur = raw
        while cur.get("inherits"):
            parent_name = str(cur["inherits"])
            if parent_name in seen:
                raise ProfileError(f"inherits cycle at {parent_name!r}")
            if len(chain) > MAX_DEPTH:
                raise ProfileError("inherits chain too deep")
            parent = pool.get(parent_name)
            if parent is None:
                raise ProfileError(f"unknown parent profile {parent_name!r}")
            seen.add(parent_name)
            chain.append(parent)
            cur = parent
        merged: dict = {}
        for item in reversed(chain):
            merged.update(item)
        merged.pop("inherits", None)
        return merged

    def flatten(self, kind: str, name: str) -> dict:
        raw = self.by_kind[kind].get(name)
        if raw is None:
            raise ProfileError(f"unknown {kind} profile {name!r}")
        return self.flatten_dict(kind, raw)

    def entries(self, kind: str, *, flatten: bool = False) -> list[dict]:
        """Instantiable presets of `kind`. Printers carry printer_model and
        nozzle_mm; process/filament carry compatible_printers plus a
        printer_model/nozzle_mm only when all their printers agree on one."""
        printers: dict[str, tuple] = {}
        if kind != "printer":
            for pname, praw in self.by_kind["printer"].items():
                try:
                    pm = self.flatten_dict("printer", praw)
                except ProfileError:
                    continue
                printers[pname] = (pm.get("printer_model"), _nozzle(pm.get("nozzle_diameter")))
        out = []
        for name, raw in sorted(self.by_kind[kind].items()):
            if str(raw.get("instantiation", "false")).lower() != "true":
                continue
            try:
                merged = self.flatten_dict(kind, raw)
            except ProfileError:
                continue
            entry: dict = {"kind": kind, "name": name}
            if kind == "printer":
                entry["printer_model"] = merged.get("printer_model")
                entry["nozzle_mm"] = _nozzle(merged.get("nozzle_diameter"))
            else:
                compat = merged.get("compatible_printers") or []
                entry["compatible_printers"] = compat
                pairs = {printers[p] for p in compat if p in printers}
                model, nozzle = next(iter(pairs)) if len(pairs) == 1 else (None, None)
                entry["printer_model"] = model
                entry["nozzle_mm"] = nozzle
            if flatten:
                entry["data"] = merged
            out.append(entry)
        return out
