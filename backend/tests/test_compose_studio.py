"""Static checks on the studio sidecar compose wiring (no docker)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    # compose.prod.yaml uses the custom `!reset` tag; treat it as plain data.
    class L(yaml.SafeLoader):
        pass

    L.add_constructor("!reset", lambda loader, node: None)
    return yaml.load((ROOT / name).read_text(), Loader=L)


def test_studio_service_shape():
    studio = _load("compose.studio.yaml")["services"]["studio"]
    assert "ports" not in studio
    assert studio["profiles"] == ["studio"]
    assert "healthcheck" in studio
    assert any(v.endswith(":/exchange") for v in studio["volumes"])
    assert studio["environment"]["SUBFOLDER"] == "/studio/"
    assert studio["environment"]["STUDIO_LAUNCHER_TOKEN"] == "${TDMM_STUDIO_LAUNCHER_TOKEN:-}"
    assert studio["environment"]["SELKIES_FILE_TRANSFERS"] == ""
    assert studio["networks"] == ["studio_net"]


def test_exchange_mounted_on_api_and_worker_io():
    services = _load("compose.yaml")["services"]
    for name in ("api", "worker-io"):
        assert any(v.endswith(":/exchange") for v in services[name]["volumes"]), name
    assert "studio_net" in services["api"]["networks"]
    assert "networks" not in services["worker-io"]
    assert "compose.studio.yaml" in _load("compose.yaml")["include"]


def test_gpu_overrides_and_prod_image():
    assert "ports" not in _load("compose.studio.dri.yaml")["services"]["studio"]
    gpu = _load("compose.studio.gpu.yaml")["services"]["studio"]
    assert gpu["runtime"] == "nvidia"
    prod = _load("compose.prod.yaml")["services"]["studio"]
    assert prod["image"].startswith("ghcr.io/metril/3d-model-manager-studio:")
