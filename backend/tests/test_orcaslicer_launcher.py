"""Launcher (docker/orcaslicer/root/app/launcher.py) health + crash tracking, subprocess stubbed."""

import importlib.util
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "docker/orcaslicer/root/app/launcher.py"


class FakeProc:
    def __init__(self, rc: int, pid: int = 4242) -> None:
        self.pid = pid
        self._rc = rc

    def wait(self) -> int:
        return self._rc


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_file_location("orcaslicer_launcher", LAUNCHER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.running = lambda: False
    return mod


def test_health_idle_before_any_launch(launcher) -> None:
    h = launcher.health()
    assert h["last_exit"] is None
    assert h["crashed"] is False
    assert h["orcaslicer_running"] is False


def test_nonzero_exit_is_a_crash(launcher) -> None:
    launcher.watch(FakeProc(139))
    h = launcher.health()
    assert (h["last_exit"], h["crashed"]) == (139, True)


def test_clean_exit_is_not_a_crash(launcher) -> None:
    launcher.watch(FakeProc(0))
    assert launcher.health()["crashed"] is False


def test_stop_by_us_is_not_a_crash(launcher, monkeypatch) -> None:
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: None)
    launcher.child_pid = 4242
    launcher.kill(grace=0)
    launcher.watch(FakeProc(-15))
    h = launcher.health()
    assert (h["last_exit"], h["crashed"]) == (-15, False)
