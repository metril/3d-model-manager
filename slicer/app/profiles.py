"""Index of OrcaSlicer's bundled `resources/profiles/<Vendor>` presets and
`inherits` flattening (same-kind, by name; cycle- and depth-guarded)."""

from __future__ import annotations

import json
from pathlib import Path

KINDS = ("printer", "process", "filament")
_DIR_BY_KIND = {"printer": "machine", "process": "process", "filament": "filament"}
MAX_DEPTH = 16
# Lookup tables shipped beside the presets; not presets themselves.
_HELPER_STEMS = {"filament_name_map", "filaments_color_codes", "filament_id_map"}


# Orca/Prusa-family keys that make the slicer run external commands or contact
# hosts. Client-supplied profiles are untrusted, so these are always dropped:
# `post_process` executes arbitrary scripts on the exported G-code.
UNSAFE_KEYS = frozenset(
    {
        "post_process",
        "print_host",
        "print_host_webui",
        "printhost_apikey",
        "printhost_authorization_type",
        "printhost_cafile",
        "printhost_password",
        "printhost_port",
        "printhost_ssl_ignore_revoke",
        "printhost_user",
        "host_type",
    }
)


def strip_unsafe(profile: dict) -> dict:
    """Copy of `profile` without script/command/host keys (see UNSAFE_KEYS)."""
    return {k: v for k, v in profile.items() if k not in UNSAFE_KEYS}


class ProfileError(ValueError):
    """Unknown parent, inherits cycle, or chain too deep."""


def _nozzle(value) -> float | None:
    try:
        return float(value[0] if isinstance(value, list) and value else value)
    except (TypeError, ValueError):
        return None


class ProfileIndex:
    def __init__(self, root: Path | None = None, vendors: list[str] | None = None) -> None:
        """`root` is one vendor dir (e.g. .../BBL) or, with `vendors`, the
        profiles dir holding one folder per vendor."""
        self.by_kind: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self.vendor: dict[str, dict[str, str]] = {k: {} for k in KINDS}
        if root is None:
            return
        if vendors is None:
            self._load(Path(root), Path(root).name or "BBL")
        else:
            for v in vendors:
                self._load(Path(root) / v, v)

    def _load(self, root: Path, vendor: str) -> None:
        for kind in KINDS:
            folder = root / _DIR_BY_KIND[kind]
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*.json")):
                if path.stem in _HELPER_STEMS:
                    continue
                try:
                    raw = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if isinstance(raw, dict):
                    name = str(raw.get("name", path.stem))
                    rel = path.relative_to(folder).parts
                    self.by_kind[kind][name] = raw
                    self.vendor[kind][name] = rel[0] if len(rel) > 1 else vendor

    def _resolve(self, kind: str, raw: dict, path: frozenset[str], depth: int) -> dict:
        """Merge order: inherited base, each `include` in listed order, then
        `raw`'s own keys (child wins). `inherits`/`include` are dropped."""
        if depth > MAX_DEPTH:
            raise ProfileError("inherits chain too deep")
        pool = self.by_kind[kind]

        def target(ref, label: str) -> dict:
            ref = str(ref)
            if ref in path:
                raise ProfileError(f"inherits cycle at {ref!r}")
            found = pool.get(ref)
            if found is None:
                raise ProfileError(f"unknown {label} profile {ref!r}")
            return self._resolve(kind, found, path | {ref}, depth + 1)

        merged: dict = {}
        if raw.get("inherits"):
            merged.update(target(raw["inherits"], "parent"))
        includes = raw.get("include") or []
        if isinstance(includes, str):
            includes = [includes]
        for ref in includes:
            merged.update(target(ref, "include"))
        merged.update(raw)
        merged.pop("inherits", None)
        merged.pop("include", None)
        return merged

    def flatten_dict(self, kind: str, raw: dict) -> dict:
        """Resolve `raw`'s `inherits` chain and `include` templates against the
        bundled presets of `kind`; child keys win. Used for bundled and
        user-posted profiles."""
        merged = self._resolve(kind, raw, frozenset({str(raw.get("name", ""))}), 0)
        return strip_unsafe(merged)

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
            entry: dict = {"kind": kind, "name": name, "vendor": self.vendor[kind].get(name, "BBL")}
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
