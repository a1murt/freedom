"""Admin tooling (package "admin", see docs/arch/admin.md): SQLite snapshots of the live database.

API: cqa/routers/admin.py (``/api/admin/snapshots``). CLI: ``python -m cqa.admin.snapshots list|create|restore|delete``.
Names below resolve lazily (PEP 562) so ``python -m cqa.admin.snapshots`` does not import the module twice.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "COUNTED_TABLES",
    "NAME_PATTERN",
    "PROTECTED",
    "DatabaseMissing",
    "InsufficientDisk",
    "InvalidName",
    "NotSupported",
    "PageSizeMismatch",
    "ProtectedSnapshot",
    "SnapshotBusy",
    "SnapshotCorrupt",
    "SnapshotError",
    "SnapshotExists",
    "SnapshotNotFound",
    "create_snapshot",
    "default_name",
    "delete_snapshot",
    "get_snapshot",
    "list_snapshots",
    "live_info",
    "resolve_sqlite_path",
    "restore_snapshot",
    "snapshot_file",
    "validate_name",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        return getattr(import_module(".snapshots", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
