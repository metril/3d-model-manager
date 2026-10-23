import pytest

from app.printers.capabilities import capabilities_for, model_key
from app.printers.stages import stage_name
from app.schemas.printers import PrinterOut, seed_build_volume_mm


@pytest.mark.parametrize(
    ("model", "serial", "key"),
    [
        ("A1 mini", None, "a1mini"),
        ("Bambu Lab A1 Mini", None, "a1mini"),
        ("N1", None, "a1mini"),
        ("A1", None, "a1"),
        ("X1 Carbon", None, "x1c"),
        ("BL-P001", None, "x1c"),
        ("O1D", None, "h2d"),
        (None, "0309ABC123", "a1mini"),
        ("weird", "01P00A123456789", "p1s"),
        ("A1", "01P00A123456789", "a1"),  # model beats serial
        (None, None, None),
        ("", "ZZZ", None),
    ],
)
def test_model_key(model, serial, key):
    assert model_key(model, serial) == key


def test_a1_mini_caps_verified_and_minimal():
    c = capabilities_for("A1 mini")
    assert c.verified is True and c.camera == "a1"
    assert not any(
        [
            c.chamber_heater,
            c.chamber_temp,
            c.airduct,
            c.door,
            c.dual_nozzle,
            c.aux_fan,
            c.chamber_fan,
            c.lidar,
            c.ams_drying,
        ]
    )
    assert (c.max_bed, c.max_nozzle) == (80, 300)


def test_other_models_unverified_and_unknown_default():
    assert capabilities_for("H2D").dual_nozzle and not capabilities_for("H2D").verified
    assert capabilities_for("X1C").camera == "rtsp"
    u = capabilities_for("Mystery")
    assert u.model_key is None and u.camera == "none" and u.verified is False


def test_stage_name():
    assert stage_name(0) == "Printing"
    assert stage_name("7") == "Heating nozzle"
    assert stage_name(255) is None and stage_name(-1) is None and stage_name(None) is None
    assert stage_name(999) == "Unknown stage (999)"


def test_printer_out_carries_capabilities():
    class P:
        id, name, kind, host, serial = 1, "p", "bambu_lan", "h", "0309ABC"
        model, enabled, options, access_code_enc = "A1 mini", True, {}, "x"
        build_volume_mm = seed_build_volume_mm("A1 mini")

    out = PrinterOut.from_model(P())
    assert out.capabilities.model_key == "a1mini" and out.capabilities.verified is True
    assert out.total_print_hours is None
