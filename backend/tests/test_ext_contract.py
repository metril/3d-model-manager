"""Contract between the browser extension and the backend: every endpoint the
extension uses (``extension/test/contract.json``, also asserted by the
extension's own tests) must exist in the OpenAPI schema with the listed
request/response fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import create_app

CONTRACT = Path(__file__).resolve().parents[2] / "extension" / "test" / "contract.json"
ENTRIES = json.loads(CONTRACT.read_text())


@pytest.fixture(scope="module")
def spec() -> dict:
    return create_app().openapi()


def _resolve(spec: dict, schema: dict | None) -> dict:
    """Follow ``$ref`` / unwrap array ``items`` / pick the object branch of anyOf."""
    while schema:
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        elif schema.get("type") == "array":
            schema = schema.get("items")
        elif "anyOf" in schema:
            schema = next(
                (s for s in schema["anyOf"] if "$ref" in s or s.get("type") == "object"), None
            )
        else:
            break
    return schema or {}


def _find_path(spec: dict, path: str) -> dict:
    for candidate in (f"/api{path}", path):
        if candidate in spec["paths"]:
            return spec["paths"][candidate]
    raise AssertionError(f"{path} missing from OpenAPI")


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: f"{e['method']} {e['path']}")
def test_extension_endpoint_in_openapi(spec: dict, entry: dict):
    op = _find_path(spec, entry["path"]).get(entry["method"].lower())
    assert op is not None, f"{entry['method']} {entry['path']} missing"

    if entry["request_fields"]:
        body = op["requestBody"]["content"]["application/json"]["schema"]
        props = _resolve(spec, body).get("properties", {})
        missing = set(entry["request_fields"]) - set(props)
        assert not missing, f"request fields missing: {missing}"

    if entry["response_fields"]:
        ok = next(v for k, v in op["responses"].items() if k.startswith("2"))
        schema = ok["content"]["application/json"]["schema"]
        props = _resolve(spec, schema).get("properties", {})
        missing = set(entry["response_fields"]) - set(props)
        assert not missing, f"response fields missing: {missing}"
