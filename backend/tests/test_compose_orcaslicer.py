"""Static checks on the orcaslicer sidecar compose wiring (no docker)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    # compose.prod.yaml uses the custom `!reset` tag; treat it as plain data.
    class L(yaml.SafeLoader):
        pass

    L.add_constructor("!reset", lambda loader, node: None)
    return yaml.load((ROOT / name).read_text(), Loader=L)


def test_orcaslicer_service_shape():
    orcaslicer = _load("compose.orcaslicer.yaml")["services"]["orcaslicer"]
    assert "ports" not in orcaslicer
    assert orcaslicer["profiles"] == ["orcaslicer"]
    assert "healthcheck" in orcaslicer
    assert any(v.endswith(":/exchange") for v in orcaslicer["volumes"])
    assert orcaslicer["environment"]["SUBFOLDER"] == "/slicer/"
    assert (
        orcaslicer["environment"]["ORCASLICER_LAUNCHER_TOKEN"]
        == "${TDMM_ORCASLICER_LAUNCHER_TOKEN:-}"
    )
    assert orcaslicer["environment"]["SELKIES_FILE_TRANSFERS"] == ""
    assert orcaslicer["networks"] == ["orcaslicer_net"]


def test_exchange_mounted_on_api_and_worker_io():
    services = _load("compose.yaml")["services"]
    for name in ("api", "worker-io"):
        assert any(v.endswith(":/exchange") for v in services[name]["volumes"]), name
    assert "orcaslicer_net" in services["api"]["networks"]
    assert "networks" not in services["worker-io"]
    assert "compose.orcaslicer.yaml" in _load("compose.yaml")["include"]


def test_gpu_overrides_and_prod_image():
    assert "ports" not in _load("compose.orcaslicer.dri.yaml")["services"]["orcaslicer"]
    gpu = _load("compose.orcaslicer.gpu.yaml")["services"]["orcaslicer"]
    assert gpu["runtime"] == "nvidia"
    prod = _load("compose.prod.yaml")["services"]["orcaslicer"]
    assert prod["image"].startswith("ghcr.io/metril/3d-model-manager-orcaslicer:")


def _launcher():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "orcaslicer_launcher", ROOT / "docker/orcaslicer/root/app/launcher.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launcher_targets_orcaslicer():
    m = _launcher()
    assert m.BIN == "/opt/orcaslicer/bin/orca-slicer"
    assert m.PATTERN == "^/opt/orcaslicer/bin/orca-slicer"
    assert m.APPRUN == "/opt/orcaslicer/AppRun"
    assert m.CONF == "/config/.config/OrcaSlicer/OrcaSlicer.conf"


def test_stale_bambu_autostart_is_replaced(tmp_path):
    import subprocess

    cfg, dfl = tmp_path / "config", tmp_path / "defaults"
    (cfg / ".config/labwc").mkdir(parents=True)
    (cfg / ".config/openbox").mkdir(parents=True)
    dfl.mkdir()
    (dfl / "autostart_wayland").write_text("/opt/orcaslicer/AppRun\n")
    (dfl / "autostart").write_text("/opt/orcaslicer/AppRun # x11\n")
    (cfg / ".config/labwc/autostart").write_text("/opt/bambustudio/AppRun\n")
    (cfg / ".config/openbox/autostart").write_text("# user\nsomething-else\n")
    script = ROOT / "docker/orcaslicer/root/custom-cont-init.d/15-orcaslicer-autostart"
    subprocess.run(
        ["bash", "-c", script.read_text().split("\n", 1)[1]],
        env={
            "ORCASLICER_CFG_DIR": str(cfg),
            "ORCASLICER_DEFAULTS_DIR": str(dfl),
            "PATH": "/usr/bin:/bin",
        },
        check=True,
    )
    assert (cfg / ".config/labwc/autostart").read_text() == "/opt/orcaslicer/AppRun\n"
    assert "something-else" in (cfg / ".config/openbox/autostart").read_text()
