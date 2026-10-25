"""SQLAlchemy ORM models for tdmm.

Importing this package registers every mapped class on the shared
``Base.metadata`` so Alembic autogeneration and ``Base.metadata.create_all``
see the full schema (SPEC "Data model").
"""

from app.models.audit_log import AuditLog
from app.models.auth import (
    ApiToken,
    AuthEphemeralToken,
    Group,
    GroupMember,
    OidcProvider,
    PrinterAccess,
    Session,
    User,
    UserOtpCode,
    UserTotp,
)
from app.models.base import Base
from app.models.billing import CostCenter, LedgerEntry
from app.models.collections import (
    FollowedCollection,
    PendingImport,
    RemoteCollectionCache,
    RemoteCollectionItem,
)
from app.models.detection import FailureDetection
from app.models.drying import ScheduledDrying
from app.models.external_link import ExternalLink
from app.models.home_assistant import HaLink
from app.models.inventory import (
    ColorCatalogEntry,
    LocationHaSensor,
    Spool,
    SpoolmanPush,
    SpoolmanSyncState,
    SpoolSlot,
    SpoolSupplier,
    SpoolUsage,
    StorageLocation,
    Supplier,
)
from app.models.kprofiles import KProfileNote, SpoolKProfile
from app.models.library import (
    Blob,
    Category,
    File,
    Model,
    Note,
    Print,
    PrintBatch,
    PrintQueueEntry,
    Revision,
    Tag,
    model_tags,
)
from app.models.notify import (
    NotificationChannel,
    NotificationLog,
    NotificationTemplate,
    UserEmailPreference,
)
from app.models.ops import BackupRun, MaintenanceLog, MaintenanceTask, SmartPlug
from app.models.overlay_token import OverlayToken
from app.models.printing import AmsLabel, PrintArchive, Printer, PrintJob
from app.models.processing import AssemblyThumb, BlobMeta, Derivative
from app.models.projects import Project, ProjectAttachment, ProjectEvent, ProjectItem
from app.models.sensors import PrinterSensorSample
from app.models.slicing import PrintRecipe, SlicerProfile
from app.models.storage import FileLocation, StorageBackendRow
from app.models.system import Import, Job, ScanRun, Setting
from app.models.virtual_printer import VirtualPrinter

__all__ = [
    "BackupRun",
    "MaintenanceLog",
    "MaintenanceTask",
    "SmartPlug",
    "ApiToken",
    "AuditLog",
    "AssemblyThumb",
    "Base",
    "Blob",
    "BlobMeta",
    "Category",
    "CostCenter",
    "Derivative",
    "ExternalLink",
    "OverlayToken",
    "File",
    "FileLocation",
    "FollowedCollection",
    "Group",
    "GroupMember",
    "HaLink",
    "VirtualPrinter",
    "Import",
    "Job",
    "LedgerEntry",
    "Model",
    "Note",
    "NotificationChannel",
    "NotificationLog",
    "NotificationTemplate",
    "AuthEphemeralToken",
    "OidcProvider",
    "UserOtpCode",
    "UserEmailPreference",
    "UserTotp",
    "PendingImport",
    "Print",
    "AmsLabel",
    "ScheduledDrying",
    "KProfileNote",
    "SpoolKProfile",
    "Printer",
    "PrinterAccess",
    "FailureDetection",
    "PrinterSensorSample",
    "PrintArchive",
    "PrintBatch",
    "Project",
    "ProjectAttachment",
    "ProjectEvent",
    "ProjectItem",
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
    "ColorCatalogEntry",
    "LocationHaSensor",
    "Spool",
    "SpoolSupplier",
    "StorageLocation",
    "Supplier",
    "SpoolSlot",
    "SpoolmanPush",
    "SpoolmanSyncState",
    "SpoolUsage",
    "StorageBackendRow",
    "Tag",
    "User",
    "model_tags",
]
