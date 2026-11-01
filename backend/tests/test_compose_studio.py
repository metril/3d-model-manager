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
    assert studio["environment"]["SUBFOLDER"] == "/slicer/"
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


def _launcher():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "studio_launcher", ROOT / "docker/studio/root/app/launcher.py"
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
    script = ROOT / "docker/studio/root/custom-cont-init.d/15-studio-autostart"
    subprocess.run(
        ["bash", "-c", script.read_text().split("\n", 1)[1]],
        env={"STUDIO_CFG_DIR": str(cfg), "STUDIO_DEFAULTS_DIR": str(dfl), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    assert (cfg / ".config/labwc/autostart").read_text() == "/opt/orcaslicer/AppRun\n"
    assert "something-else" in (cfg / ".config/openbox/autostart").read_text()
