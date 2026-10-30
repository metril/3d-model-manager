import io
import zipfile
from types import SimpleNamespace

import pytest
import redis as redis_lib

from app.printers import pause_eta

GCODE = """\
; HEADER
M400 U1
; layer num/total_layer_count: 1/100
M73 P1 R120
G1 X1
; layer num/total_layer_count: 2/100
M73 P40 R78
;PAUSE_PRINT
M400 U1
G1 X2
; layer num/total_layer_count: 3/100
M73 P50 R60
G1 X3
; layer num/total_layer_count: 5/100
M73 P80 R20
M400 U1
G1 X4
"""


def _pub(**kw):
    base = {"gcode_state": "RUNNING", "layer_num": 1, "mc_remaining_time": 110, "speed_level": 2}
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def r(redis_url):
    client = redis_lib.Redis.from_url(redis_url)
    pid = 987001
    keys = (pause_eta.points_key(pid), pause_eta.warned_key(pid, 7), pause_eta.warned_key(pid, 8))
    client.delete(*keys)
    yield client, pid
    client.delete(*keys)


def test_parser_finds_two_pauses_and_ignores_start_gcode():
    pts = pause_eta.parse_pause_points(GCODE.splitlines())
    assert pts == [{"layer": 2, "remaining_min": 78}, {"layer": 5, "remaining_min": 20}]


def test_pause_points_from_3mf_reads_plate():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", GCODE)
    assert len(pause_eta.pause_points_from_3mf(buf.getvalue(), 1)) == 2
    assert pause_eta.pause_points_from_3mf(buf.getvalue(), 2) == []
    assert pause_eta.pause_points_from_3mf(b"nope", 1) == []


def test_next_pause_eta_is_printer_remaining_minus_slicer_remaining():
    pts = pause_eta.parse_pause_points(GCODE.splitlines())
    assert pause_eta.next_pause(_pub(), pts) == {"layer": 2, "eta_s": 32 * 60}
    assert pause_eta.next_pause(_pub(layer_num=2, mc_remaining_time=78), pts) == {
        "layer": 5,
        "eta_s": 58 * 60,
    }
    assert pause_eta.next_pause(_pub(layer_num=5, mc_remaining_time=20), pts) is None
    assert pause_eta.next_pause(_pub(gcode_state="PAUSE"), pts) is None
    assert pause_eta.next_pause(_pub(), []) is None
    assert pause_eta.next_pause(_pub(mc_remaining_time=0, layer_num=0), pts) == {
        "layer": 2,
        "eta_s": None,
    }
    assert pause_eta.next_pause(_pub(speed_level=3), pts) == {"layer": 2, "eta_s": None}


def test_heads_up_minutes_clamps():
    assert pause_eta.heads_up_minutes(None) == 5
    assert pause_eta.heads_up_minutes("x") == 5
    assert pause_eta.heads_up_minutes(0) == 1
    assert pause_eta.heads_up_minutes(999) == 120
    assert pause_eta.heads_up_minutes(10) == 10


def test_warning_is_one_shot_per_job_and_layer(r):
    client, pid = r
    assert pause_eta.claim_warning(client, pid, 7, 2) is True
    assert pause_eta.claim_warning(client, pid, 7, 2) is False
    assert pause_eta.claim_warning(client, pid, 7, 5) is True
    assert pause_eta.claim_warning(client, pid, 8, 2) is True


def test_points_roundtrip(r):
    client, pid = r
    pts = [{"layer": 2, "remaining_min": 78}]
    pause_eta.store_points(client, pid, 7, pts)
    assert pause_eta.load_points(client, pid) == (7, pts)
    pause_eta.clear_points(client, pid)
    assert pause_eta.load_points(client, pid) == (None, [])
