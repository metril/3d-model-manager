import httpx


async def test_crud_roundtrip(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    r = await c.post(
        "/api/external-links", json={"name": "B", "url": "https://b.example", "sort_order": 2}
    )
    assert r.status_code == 201
    b = r.json()
    assert b["open_mode"] == "iframe"
    a = (
        await c.post(
            "/api/external-links",
            json={"name": "A", "url": "https://a.example", "sort_order": 1, "open_mode": "newtab"},
        )
    ).json()

    assert [x["name"] for x in (await c.get("/api/external-links")).json()] == ["A", "B"]

    r = await c.patch(f"/api/external-links/{b['id']}", json={"name": "B2", "sort_order": 0})
    assert r.status_code == 200 and r.json()["name"] == "B2"
    assert [x["name"] for x in (await c.get("/api/external-links")).json()] == ["B2", "A"]

    assert (await c.delete(f"/api/external-links/{a['id']}")).status_code == 204
    assert (await c.delete(f"/api/external-links/{a['id']}")).status_code == 404
    assert (await c.patch("/api/external-links/999999", json={"name": "x"})).status_code == 404


async def test_rejects_bad_url_and_mode(authenticated_client: httpx.AsyncClient) -> None:
    c = authenticated_client
    for bad in ("javascript:alert(1)", "ftp://x", "not a url"):
        r = await c.post("/api/external-links", json={"name": "x", "url": bad})
        assert r.status_code == 422
    r = await c.post(
        "/api/external-links", json={"name": "x", "url": "https://x", "open_mode": "popup"}
    )
    assert r.status_code == 422


async def test_viewer_reads_but_cannot_write(
    authenticated_client: httpx.AsyncClient, viewer_client: httpx.AsyncClient
) -> None:
    await authenticated_client.post("/api/external-links", json={"name": "A", "url": "https://a"})
    r = await viewer_client.get("/api/external-links")
    assert r.status_code == 200 and len(r.json()) == 1
    assert (
        await viewer_client.post("/api/external-links", json={"name": "x", "url": "https://x"})
    ).status_code == 403
