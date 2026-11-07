from app.services.profile_sanitize import clamp_key, parse_range_error, sanitize_profile

PROD_MSG = "tree_support_wall_count: -1 not in range [0.000000,2.000000]"


def test_parse_prod_message():
    assert parse_range_error(f"Param values in 3mf/config error:\n{PROD_MSG}\nrun") == (
        "tree_support_wall_count",
        0.0,
        2.0,
    )
    assert parse_range_error("nothing") is None


def test_negative_dropped_all_shapes():
    for v in ("-1", ["-1"], -1, -1.0):
        out, changed = sanitize_profile({"tree_support_wall_count": v, "keep": "x"})
        assert "tree_support_wall_count" not in out and out["keep"] == "x"
        assert changed == ["tree_support_wall_count"]


def test_above_hi_clamped_preserving_type():
    assert sanitize_profile({"tree_support_wall_count": "5"})[0] == {"tree_support_wall_count": "2"}
    assert sanitize_profile({"tree_support_wall_count": ["5"]})[0] == {
        "tree_support_wall_count": ["2"]
    }
    assert sanitize_profile({"tree_support_wall_count": 9})[0] == {"tree_support_wall_count": 2}


def test_idempotent_and_pure():
    src = {"tree_support_wall_count": "-1"}
    out, _ = sanitize_profile(src)
    assert src == {"tree_support_wall_count": "-1"}
    assert sanitize_profile(out) == (out, [])
    assert sanitize_profile({"tree_support_wall_count": "1"})[1] == []


def test_clamp_key():
    d = {"a": "-1"}
    assert clamp_key(d, "a", 0, 3) and d == {}
    assert not clamp_key(d, "a", 0, 3)
