import pytest

from app.services.oidc import extract_claim, safe_return_to


def test_extract_claim_dotted():
    assert extract_claim({"realm_access": {"roles": ["a", "b"]}}, "realm_access.roles") == [
        "a",
        "b",
    ]


def test_extract_claim_string_and_missing():
    assert extract_claim({"groups": "x"}, "groups") == ["x"]
    assert extract_claim({}, "groups") == []
    assert extract_claim({"a": 1}, "a.b") == []


@pytest.mark.parametrize("v", ["//evil.com", "https://evil.com", "/\\evil.com", None, ""])
def test_safe_return_to_rejects(v):
    assert safe_return_to(v) == "/"


def test_safe_return_to_keeps_path():
    assert safe_return_to("/models/x?y=1") == "/models/x?y=1"
