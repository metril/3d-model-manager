"""Followed remote collections + the review queue (M8 H "import your saved
models and keep them synced").

A ``FollowedCollection`` is a remote list the user follows -- a MakerWorld
collection, a Thingiverse/Printables collection, or a site's "likes" pseudo-list
-- identified by ``(site, list_id)``. Each carries its own ``mode``: an
``auto`` list imports newly discovered items straight away, a ``review`` list
parks them in ``pending_imports`` for one-click approval instead.

The sync task never needs a per-list cursor: item identity is
``(site, external_id)`` and ``app.services.import_dedup`` already answers "is
this in the library?", so re-walking a list is naturally idempotent. That keeps
the schema free of site-specific pagination state.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, str_enum
from app.models.enums import CollectionSyncMode, ImportSite


class FollowedCollection(Base):
    """A remote list the user follows and wants kept in sync."""

    __tablename__ = "followed_collections"
    __table_args__ = (
        UniqueConstraint("site", "list_id", name="uq_followed_collections_site_list"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    site: Mapped[ImportSite] = mapped_column(str_enum(ImportSite, "import_site"), nullable=False)
    # The remote list's own identifier (a collection id, or a sentinel like
    # "likes" for a site's favourites pseudo-list).
    list_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # "collection" | "likes"
    title: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[CollectionSyncMode] = mapped_column(
        str_enum(CollectionSyncMode, "collection_sync_mode"), nullable=False
    )
    last_synced_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class PendingImport(Base):
    """An item a ``review``-mode sync discovered but did NOT import. Approving
    one just POSTs its ``url`` through the normal import path (which is itself
    dedup-guarded), then deletes this row.

    ``(site, external_id)`` is unique SITE-WIDE (R7 T1), not just per
    collection: item identity for dedup purposes has always been ``(site,
    external_id)`` (``app.services.import_dedup``), and a sync that discovers
    the same item through two different followed lists must not queue it
    twice. ``app.services.collections.add_pending_sync``/``drop_pending_sync``
    enforce this at the application level; the constraint below is the DB-level
    backstop. The older ``(collection_id, external_id)`` constraint is now
    implied-redundant but kept -- harmless, and cheaper to leave than to prove
    nothing relies on it."""

    __tablename__ = "pending_imports"
    __table_args__ = (
        UniqueConstraint("collection_id", "external_id", name="uq_pending_imports_collection_item"),
        UniqueConstraint("site", "external_id", name="uq_pending_imports_site_external_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("followed_collections.id", ondelete="CASCADE"), nullable=False
    )
    site: Mapped[ImportSite] = mapped_column(str_enum(ImportSite, "import_site"), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class RemoteCollectionCache(Base):
    """A read-through cache of a site's REAL collection list (M10 escape
    hatch), keyed by ``(site, list_id)``. Exists because MakerWorld's own
    per-collection enumeration (the SSR ``collections.json`` route
    ``list_user_lists`` reads, ``app.importers.makerworld``) is intermittently
    Cloudflare-walled from a server IP -- when it is, the wall merges every
    one of the user's collections into a single uid-keyed aggregate.

    Two independent producers keep this warm: the browser extension pushes a
    full authoritative snapshot (``POST /ext/collections``, read in the
    user's own authenticated browser where the wall isn't up), and
    ``list_user_lists`` itself upserts into it whenever the SSR route happens
    to succeed on its own, so the cache self-heals without the extension.
    Both go through ``app.services.remote_collections.replace_site_cache``
    (or its sync twin), which treats its input as the site's full current
    list and deletes anything cached for that site NOT in it.
    """

    __tablename__ = "remote_collection_cache"
    __table_args__ = (
        UniqueConstraint("site", "list_id", name="uq_remote_collection_cache_site_list"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    site: Mapped[ImportSite] = mapped_column(str_enum(ImportSite, "import_site"), nullable=False)
    list_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int | None] = mapped_column(Integer)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RemoteCollectionItem(Base):
    """Extension-pushed MEMBERSHIP of one remote collection (M10 Workstream A
    task 3) -- which models belong to a followed named collection, per the
    LAST ``POST /ext/collections/{list_id}/items`` push for that ``(site,
    list_id)``. NOT a duplicate of ``RemoteCollectionCache`` above: that
    table tracks a collection's METADATA (id/title/slug/count); this one
    tracks its CONTENTS.

    Exists because ``GET /api/v1/design-service/favorites/designs/{listId}``
    -- the endpoint ``app.importers.makerworld.MakerWorldImporter
    .list_list_items`` reads live -- serves ONLY the uid aggregate ("all
    collected models") from a server IP: a real named collection id returns
    ``200 {"total":0}`` (live-verified 2026-07-11 against 3 real ids), so a
    followed named collection would sync ZERO items forever without this.
    The browser extension fetches each collection's items from the page
    origin (its own authenticated browser session, where the Cloudflare wall
    around the server isn't up) and pushes a full replace-set here -- same
    "authoritative snapshot, not a merge" posture as
    ``RemoteCollectionCache``/``app.services.remote_collections
    .replace_site_cache``. ``list_list_items`` falls back to
    ``get_list_items`` when its live fetch comes back empty for a non-uid
    list id; a live hit (if MakerWorld ever fixes the endpoint) always wins
    over the cache.

    ``position`` records each item's order within the push (the extension
    walks the same paged endpoint the backend would, so paging the cache
    back out via ``ORDER BY position`` reproduces the original page order).
    """

    __tablename__ = "remote_collection_items"
    __table_args__ = (
        UniqueConstraint(
            "site", "list_id", "external_id", name="uq_remote_collection_items_site_list_external"
        ),
        Index("ix_remote_collection_items_site_list", "site", "list_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    site: Mapped[ImportSite] = mapped_column(str_enum(ImportSite, "import_site"), nullable=False)
    list_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
