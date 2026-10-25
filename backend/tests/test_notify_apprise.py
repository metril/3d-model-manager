import apprise
import httpx
import pytest

from app.services.notify import PROVIDERS, Message, ProviderError

MSG = Message("T", "B", "job_finished")
PHOTO = Message("T", "B", "job_finished", photo=b"\xff\xd8jpeg\xff\xd9")
CLIENT = httpx.Client()


class Fake:
    instances: list["Fake"] = []
    bad_add: set[str] = set()
    bad_notify: set[str] = set()

    def __init__(self):
        self.url = None
        self.calls: list[dict] = []
        Fake.instances.append(self)

    def add(self, url):
        if url in Fake.bad_add:
            return False
        self.url = url
        return True

    def notify(self, **kw):
        self.calls.append(kw)
        return self.url not in Fake.bad_notify


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    Fake.instances, Fake.bad_add, Fake.bad_notify = [], set(), set()
    monkeypatch.setattr(apprise, "Apprise", Fake)


def send(urls, msg=MSG, **cfg):
    PROVIDERS["apprise"].send({"urls": urls, **cfg}, msg, CLIENT)


def test_split_skips_comments_blanks_and_notifies_per_url():
    send("slack://a/b/c\n\n  # note\n  gotify://h/key  \n")
    assert [i.url for i in Fake.instances] == ["slack://a/b/c", "gotify://h/key"]
    for i in Fake.instances:
        assert len(i.calls) == 1
        c = i.calls[0]
        assert c["title"] == "T" and c["body"] == "B" and "tag" not in c and c["attach"] is None
        assert c["body_format"] == apprise.NotifyFormat.TEXT


def test_photo_attached_in_memory():
    send("slack://a/b/c", PHOTO)
    att = Fake.instances[0].calls[0]["attach"]
    assert isinstance(att, apprise.AppriseAttachment) and len(att) == 1
    assert att[0].name == "photo.jpg" and att[0].mimetype == "image/jpeg"


def test_invalid_url_names_line_not_url():
    Fake.bad_add = {"bogus://user:pw@h"}
    with pytest.raises(ProviderError) as e:
        send("slack://a/b/c\n# c\nbogus://user:pw@h")
    assert "line 3" in str(e.value) and "pw" not in str(e.value) and "bogus" not in str(e.value)
    assert not any(i.calls for i in Fake.instances)


def test_partial_failure_lists_scheme_host_only():
    Fake.bad_notify = {"discord://id:tok@hooks.example.com/path"}
    with pytest.raises(ProviderError) as e:
        send("slack://a/b/c\ndiscord://id:tok@hooks.example.com/path")
    msg = str(e.value)
    assert "discord://hooks.example.com" in msg and "tok" not in msg and "path" not in msg
    assert "slack" not in msg
    assert len(Fake.instances[0].calls) == 1  # other target still delivered


def test_notify_exception_is_redacted(monkeypatch):
    def boom(self, **kw):
        raise RuntimeError("secret-token")

    monkeypatch.setattr(Fake, "notify", boom)
    with pytest.raises(ProviderError) as e:
        send("slack://a/b/c")
    assert "secret-token" not in str(e.value)


def test_requires_urls():
    with pytest.raises(ProviderError):
        send("   ")
    with pytest.raises(ProviderError, match="no Apprise URLs"):
        send("# only a comment")


def test_no_tag_field_in_catalog():
    assert [f["name"] for f in PROVIDERS["apprise"].fields] == ["urls"]


def test_real_apprise_plumbing_without_fake(monkeypatch):
    """Unpatched apprise: add() parses the URL, notify() really POSTs (requests mocked)."""
    import requests

    monkeypatch.undo()  # drop the autouse Fake
    posts: list[tuple] = []

    class Resp:
        status_code = 200
        content = b"{}"
        text = "{}"

    def fake_post(method, url, *a, **kw):
        posts.append((url, kw.get("data")))
        return Resp()

    monkeypatch.setattr(requests, "request", fake_post)
    send("json://localhost/hook", PHOTO)
    assert posts and posts[0][0].startswith("http://localhost")


def test_catalog_lists_apprise_with_secret_urls():
    p = PROVIDERS["apprise"]
    assert p.supports_photo
    urls = next(f for f in p.fields if f["name"] == "urls")
    assert urls["secret"] and urls["multiline"] and urls["required"]
    from app.services.notify.delivery import mask_config

    assert mask_config("apprise", {"urls": "slack://x", "tag": "t"})["urls"] != "slack://x"
