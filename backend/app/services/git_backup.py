"""Optional git push of a backup's small artifacts (Q7) over each provider's
REST "contents" API: GitHub, Gitea/Forgejo (same shape) and GitLab. HTTPS
token auth only. Errors are folded into ``GitPushError`` with fixed text."""

from __future__ import annotations

import base64
from urllib.parse import quote

import httpx

TIMEOUT_S = 30.0
MAX_FILE_BYTES = 50 * 1024 * 1024


class GitPushError(Exception):
    pass


def _api_base(provider: str, base_url: str | None) -> str:
    if provider == "github":
        return "https://api.github.com"
    if not base_url:
        raise GitPushError(f"{provider} requires a base URL")
    base = base_url.rstrip("/")
    return base + ("/api/v4" if provider == "gitlab" else "/api/v1")


def push_file(
    *,
    provider: str,
    base_url: str | None,
    repo: str,
    branch: str,
    token: str,
    path: str,
    content: bytes,
    message: str,
    client: httpx.Client | None = None,
) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise GitPushError("file too large for git push")
    api = _api_base(provider, base_url)
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    b64 = base64.b64encode(content).decode()
    try:
        if provider == "gitlab":
            url = f"{api}/projects/{quote(repo, safe='')}/repository/files/{quote(path, safe='')}"
            headers = {"PRIVATE-TOKEN": token}
            body = {
                "branch": branch,
                "commit_message": message,
                "content": b64,
                "encoding": "base64",
            }
            exists = http.head(url, params={"ref": branch}, headers=headers).status_code == 200
            r = (http.put if exists else http.post)(url, json=body, headers=headers)
        else:
            url = f"{api}/repos/{repo}/contents/{quote(path)}"
            headers = {
                "Authorization": f"token {token}" if provider != "github" else f"Bearer {token}"
            }
            cur = http.get(url, params={"ref": branch}, headers=headers)
            body = {"message": message, "content": b64, "branch": branch}
            if cur.status_code == 200:
                body["sha"] = cur.json().get("sha")
            elif cur.status_code != 404:
                raise GitPushError(f"{provider} lookup returned {cur.status_code}")
            create = provider != "github" and "sha" not in body  # Gitea/Forgejo: POST creates
            r = (http.post if create else http.put)(url, json=body, headers=headers)
        if r.status_code not in (200, 201):
            raise GitPushError(f"{provider} push returned {r.status_code}")
    except httpx.HTTPError:
        raise GitPushError(f"could not reach {provider}") from None
    finally:
        if own:
            http.close()
