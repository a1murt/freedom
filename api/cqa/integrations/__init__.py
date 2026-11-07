"""External system integrations. Dynamics 365 Contact Center (Omnichannel): see dynamics.py and docs/INTEGRATION_D365.md."""
from .dynamics import (  # noqa: F401
    SOURCE,
    DynamicsParseError,
    content_hash,
    decode_documentbody,
    detect_language,
    encode_documentbody,
    enrich_conversation,
    normalize_channel,
    normalize_conversation_id,
    normalize_language,
    parse_transcript,
    sample_messages,
)
