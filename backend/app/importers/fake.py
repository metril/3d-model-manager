"""In-memory SiteImporter for orchestration tests (M5 carve-out; the mirror
of M4's FakePrinterAdapter). Holds canned metadata + a ``{filename: bytes}``
map; ``resolve_download`` hands back ``https://fake.test/dl/<filename>``
URLs that the test's httpx.MockTransport serves from the SAME byte map.
NOT auto-registered -- tests register it over a site (default THINGIVERSE)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from app.importers.base import ImportFile, ImportMetadata, ResolvedDownload, SearchResult
from app.models.enums import ImportSite

FAKE_BASE = "https://fake.test/thing/"
FAKE_DL = "https://fake.test/dl/"


@dataclass
class FakeImporter:
    site: ClassVar[ImportSite] = ImportSite.THINGIVERSE
    external_id: str = "42"
    title: str = "Fake Thing"
    description: str | None = "a fake import"
    author: str | None = "fakeuser"
    license: str | None = "CC-BY-4.0"
    cover_url: str | None = "https://fake.test/cover.png"
    # T2: gallery-image URLs to report from fetch_metadata -- empty by
    # default so existing tests (that never set this) don't suddenly start
    # attempting image downloads. Tests exercising the image-download flow
    # set this to FAKE_DL-prefixed URLs and populate matching `image_bytes`.
    image_urls: tuple[str, ...] = ()
    tags: tuple[str, ...] = ("fake", "test")
    reject_reason: str | None = None
    files: dict[str, bytes] = field(default_factory=dict)
    # T2: byte content served for `image_urls` entries -- kept SEPARATE from
    # `files` above (rather than reusing it) because `list_files()` treats
    # EVERY `files` entry as one of the model's own downloadable files;
    # gallery images are the site's own photos, never one of those.
    image_bytes: dict[str, bytes] = field(default_factory=dict)
    # Per-file profile metadata (``ImportFile.meta``), keyed by filename.
    file_meta: dict[str, dict] = field(default_factory=dict)

    def canonicalize(self, url: str) -> str | None:
        if url.startswith(FAKE_BASE):
            return url[len(FAKE_BASE) :] or self.external_id
        return None

    def fetch_metadata(self, external_id: str) -> ImportMetadata:
        return ImportMetadata(
            site=self.site,
            external_id=external_id,
            source_url=f"{FAKE_BASE}{external_id}",
            title=self.title,
            description=self.description,
            author=self.author,
            license=self.license,
            cover_url=self.cover_url,
            image_urls=list(self.image_urls),
            tags=self.tags,
            reject_reason=self.reject_reason,
        )

    def list_files(self, external_id: str) -> list[ImportFile]:
        return [
            ImportFile(
                remote_id=name,
                filename=name,
                url=f"{FAKE_DL}{name}",
                size=len(data),
                meta=self.file_meta.get(name),
            )
            for name, data in self.files.items()
        ]

    def resolve_download(self, external_id: str, file: ImportFile) -> ResolvedDownload:
        return ResolvedDownload(url=file.url or f"{FAKE_DL}{file.filename}", filename=file.filename)

    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        if not query:
            return []
        return [
            SearchResult(
                site=self.site,
                external_id=self.external_id,
                title=self.title,
                url=f"{FAKE_BASE}{self.external_id}",
                author=self.author,
                thumbnail_url=self.cover_url,
            )
        ]
