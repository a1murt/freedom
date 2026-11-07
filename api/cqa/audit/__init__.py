"""Audit trail (docs/ARCH_CONTRACTS.md §2): `record(db, action=..., ...)` for semantic events, `list_events` for reads.

Write requests are recorded automatically by `cqa.security.middleware`; see docs/arch/roles.md.
"""
from .service import compact, compact_to_size, facets, list_events, record, scrub_text, serialize_event

__all__ = ["compact", "compact_to_size", "facets", "list_events", "record", "scrub_text", "serialize_event"]
