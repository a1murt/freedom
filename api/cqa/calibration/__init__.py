"""Calibration: human gold labels, AI-vs-human agreement and run-to-run stability.

The service lives in ``cqa.calibration.service``; the HTTP API is ``cqa.routers.calibration``
(``/api/calibration/*``). Design notes, formats and examples: ``docs/arch/calibration.md``.
"""
from __future__ import annotations

from .service import (
    DEFAULT_LABELER,
    NA,
    NOTE_CODE,
    TOTAL_CODE,
    CalibrationError,
    ValidationFailed,
    VersionNotFound,
    agreement,
    canonical_value,
    default_labeler,
    export_gold,
    import_gold,
    labelers,
    list_labels,
    organizer_version,
    resolve_version,
    stability,
    upsert_labels,
)

__all__ = [
    "DEFAULT_LABELER",
    "NA",
    "NOTE_CODE",
    "TOTAL_CODE",
    "CalibrationError",
    "ValidationFailed",
    "VersionNotFound",
    "agreement",
    "canonical_value",
    "default_labeler",
    "export_gold",
    "import_gold",
    "labelers",
    "list_labels",
    "organizer_version",
    "resolve_version",
    "stability",
    "upsert_labels",
]
