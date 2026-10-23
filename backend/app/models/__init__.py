"""SQLAlchemy ORM models for tdmm.

Importing this package registers every mapped class on the shared
``Base.metadata`` so Alembic autogeneration and ``Base.metadata.create_all``
see the full schema (SPEC "Data model").
"""

from app.models.auth import (
    ApiToken,
    Group,
    GroupMember,
    OidcProvider,
    PrinterAccess,
    Session,
    User,
)
from app.models.base import Base
from app.models.billing import CostCenter, LedgerEntry
from app.models.collections import (
    FollowedCollection,
    PendingImport,
    RemoteCollectionCache,
    RemoteCollectionItem,
)
from app.models.home_assistant import HaLink
from app.models.inventory import Spool, SpoolmanSyncState, SpoolSlot, SpoolUsage
from app.models.library import (
    Blob,
    Category,
    File,
    Model,
    Note,
    Print,
    PrintQueueEntry,
    Revision,
    Tag,
    model_tags,
)
from app.models.printing import PrintArchive, Printer, PrintJob
from app.models.processing import AssemblyThumb, BlobMeta, Derivative
from app.models.slicing import PrintRecipe, SlicerProfile
from app.models.storage import FileLocation, StorageBackendRow
from app.models.system import Import, Job, ScanRun, Setting

__all__ = [
    "ApiToken",
    "AssemblyThumb",
    "Base",
    "Blob",
    "BlobMeta",
    "Category",
    "CostCenter",
    "Derivative",
    "File",
    "FileLocation",
    "FollowedCollection",
    "Group",
    "GroupMember",
    "HaLink",
    "Import",
    "Job",
    "LedgerEntry",
    "Model",
    "Note",
    "OidcProvider",
    "PendingImport",
    "Print",
    "Printer",
    "PrinterAccess",
    "PrintArchive",
    "PrintJob",
    "PrintQueueEntry",
    "PrintRecipe",
    "RemoteCollectionCache",
    "RemoteCollectionItem",
    "Revision",
    "ScanRun",
    "Session",
    "Setting",
    "SlicerProfile",
    "Spool",
    "SpoolSlot",
    "SpoolmanSyncState",
    "SpoolUsage",
    "StorageBackendRow",
    "Tag",
    "User",
    "model_tags",
]
