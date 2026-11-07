"""Dynamics 365 Contact Center (Omnichannel) transcripts -> organizer record format.

What we rely on (Microsoft Learn, "Download Dynamics 365 Contact Center transcripts in bulk" and the Transcript
(msdyn_transcript) table reference; details, sources and a sample in docs/INTEGRATION_D365.md §2):

- every conversation (``msdyn_ocliveworkitem``, primary key ``activityid``) gets one ``msdyn_transcript`` row whose lookup
  ``msdyn_liveworkitemidid`` (Web API ``_msdyn_liveworkitemidid_value``) points back to the conversation;
- the transcript text lives in a Dataverse note (``annotation``) with ``objecttypecode = 'msdyn_transcript'`` and
  ``objectid`` = the transcript; ``documentbody`` holds the transcript **base64-encoded**. Attachments are further notes
  with ``objecttypecode = 'msdyn_ocliveworkitem'`` (n attachments -> n + 1 notes per conversation);
- the decoded document is JSON: a list of messages. Fields we read: ``content``, ``contentType``, ``createdDateTime``
  (fallback ``created``, then the epoch-millisecond ``id``), ``from`` = ``{user | application | ...: {displayName, id}}``
  or ``null``, ``tags`` (comma-separated: ``public`` = agent to customer, ``private`` = agent to agent, ``system``),
  ``isControlMessage`` (XML events such as ``<addmember>`` / ``<deletemember>`` with ``<target>8:orgid:<agent id>``),
  ``attachments`` (``name``, ``annotationid``) and ``deleted``.

``parse_transcript()`` accepts (a) ``{"annotation": {"documentbody": "<base64>"}}`` or ``{"documentbody": "<base64>"}``,
(b) the decoded transcript JSON (a list, a wrapper object or a JSON string), or (c) a plain message list (D365 messages or
simplified ``{speaker|role, text|content, timestamp}`` items). It returns the organizer record
``{conversation_id, channel, language, transcript: [{timestamp, speaker, text}], audio_url}`` that
``cqa.ingest.service.upsert_record`` stores, plus a ``"d365"`` key with parse statistics (pop it before upserting).
Pure functions except ``enrich_conversation()``, which post-processes the ORM conversation after the upsert.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import gzip
import hashlib
import html
import io
import itertools
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..ingest.normalize import parse_iso

SOURCE = "dynamics365"

PRIVATE_MODES = ("system", "skip")  # private (agent-to-agent) messages: system turn "[internal] ..." or dropped
CONTROL_MODES = ("events", "all", "none")  # control messages: join/leave events only, every one, or none
ORDER_MODES = ("auto", "keep", "chronological")  # auto = original order unless the whole list is newest-first

MAX_DOCUMENTBODY_CHARS = 20_000_000  # base64 text (~15 MB decoded); a long chat transcript is well under 1 MB
MAX_DECODED_BYTES = 30_000_000
MAX_MESSAGES = 5000
MAX_TEXT_CHARS = 10_000

VOICE_STREAM_CODES = frozenset({"192370000", "192440000"})  # msdyn_streamsource / msdyn_channel: Voice, Voice call
INTERNAL_PREFIX = "[internal]"

KZ_LETTERS = frozenset("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
KZ_MIN_SHARE = 0.02  # Kazakh-specific letters per Cyrillic letter; Kazakh text ~10-15 %, a Kazakh name in Russian text <1 %


class DynamicsParseError(ValueError):
    """The payload is not a usable transcript. ``code``/``message`` (Russian) go into the API error envelope."""

    def __init__(self, code: str, message: str, *, field: str | None = None, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.status = status

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            detail["errors"] = [{"field": self.field, "message": self.message}]
        return detail


# --------------------------------------------------------------------------------------------- small helpers


def _ci(d: Any, *keys: str) -> Any:
    """Case-insensitive lookup of the first present key."""
    if not isinstance(d, dict):
        return None
    for key in keys:
        if key in d:
            return d[key]
    lower = {str(k).lower(): v for k, v in d.items()}
    for key in keys:
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def _s(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _epoch(value: float) -> tuple[str, datetime | None]:
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        dt = datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return str(value), None
    return dt.isoformat(timespec="milliseconds"), dt


def _has_letters(texts: Iterable[str]) -> bool:
    return any(ch.isalpha() for text in texts for ch in text)


def _truthy(value: Any) -> bool:
    """JSON booleans as Dataverse and Power Automate send them (true, "true", 1)."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1)


# --------------------------------------------------------------------------------------------- normalizers


_LANG_ALIASES = {"RU": "RU", "RUS": "RU", "RUSSIAN": "RU", "РУ": "RU", "РУС": "RU",
                 "KZ": "KZ", "KK": "KZ", "KAZ": "KZ", "KAZAKH": "KZ", "ҚАЗ": "KZ", "КАЗ": "KZ", "KAZ-KZ": "KZ",
                 "EN": "EN", "ENG": "EN", "ENGLISH": "EN"}
_LCID = {1049: "RU", 1087: "KZ", 1033: "EN", 2057: "EN"}


def normalize_language(value: Any) -> str | None:
    """'ru', 'ru-RU', 'kk', 'kk-KZ', 'kz', 'en-US', LCID 1049/1087/1033 -> RU | KZ | EN; empty -> None."""
    if value is None or isinstance(value, bool):
        return None if value is None else _bad_language(value)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        code = _LCID.get(int(value))
        return code or _bad_language(value)
    text = str(value).strip().upper().replace("_", "-")
    if not text:
        return None
    if text in _LANG_ALIASES:
        return _LANG_ALIASES[text]
    head = text.split("-", 1)[0]
    if head in _LANG_ALIASES:
        return _LANG_ALIASES[head]
    return _bad_language(value)


def _bad_language(value: Any) -> str:
    raise DynamicsParseError("invalid_language", f"Неизвестный язык «{value}»: ожидается RU, KZ или EN", field="language")


_VOICE_WORDS = ("voice", "call", "phone", "telephon", "звон", "голос", "телефон", "қоңырау")
_CHAT_WORDS = ("chat", "чат", "sms", "messag", "whatsapp", "telegram", "teams", "facebook", "line", "wechat", "twitter",
               "apple", "google", "custom", "lcw", "web", "copilot", "chatgpt", "video", "co-browse", "screen")


def normalize_channel(value: Any, *, strict: bool = False) -> str | None:
    """Our two channels from a request value, a D365 channel code (msdyn_channel / msdyn_streamsource) or its label."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple, set)):
        codes = [str(v).strip() for v in value]
    elif isinstance(value, (int, float)):
        codes = [str(int(value))]
    else:
        text = str(value).strip()
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if parts and all(p.isdigit() for p in parts):
            codes = parts
        else:
            low = text.lower()
            if low in ("agent chat", "voice call"):
                return low
            if any(w in low for w in _VOICE_WORDS):
                return "voice call"
            if any(w in low for w in _CHAT_WORDS):
                return "agent chat"
            if strict:
                raise DynamicsParseError("invalid_channel", f"Неизвестный канал «{value}»: ожидается «agent chat» или «voice call»",
                                         field="channel")
            return None
    if any(c in VOICE_STREAM_CODES for c in codes):
        return "voice call"
    if any(c.startswith("192") for c in codes):
        return "agent chat"
    if strict:
        raise DynamicsParseError("invalid_channel", f"Неизвестный код канала «{value}»", field="channel")
    return None


_GUID_RE = re.compile(r"^\{?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}?$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$")


def normalize_conversation_id(value: Any, *, field: str = "conversation_id") -> str | None:
    """GUIDs lose braces and case ({A1B2...} from formContext.data.entity.getId() == a1b2...); other ids are kept as sent."""
    if isinstance(value, dict):  # a lookup object
        value = _ci(value, "id", "value", "activityid")
    text = _s(value)
    if text is None:
        return None
    m = _GUID_RE.match(text)
    if m:
        return m.group(1).lower()
    if not _ID_RE.match(text):
        raise DynamicsParseError("invalid_conversation_id",
                                 f"Недопустимый идентификатор диалога «{text[:80]}»: до 64 символов, латиница, цифры и . _ : @ -",
                                 field=field)
    return text


_ROLE_ALIASES = {
    "client": "client", "customer": "client", "end user": "client", "enduser": "client", "end_user": "client",
    "visitor": "client", "guest": "client", "contact": "client", "consumer": "client", "c2": "client", "клиент": "client",
    "operator": "operator", "agent": "operator", "user": "operator", "csr": "operator", "representative": "operator",
    "service representative": "operator", "human agent": "operator", "employee": "operator", "c1": "operator",
    "оператор": "operator", "агент": "operator", "сотрудник": "operator",
    "bot": "bot", "application": "bot", "virtual agent": "bot", "virtualagent": "bot", "copilot": "bot", "assistant": "bot",
    "ivr": "bot", "chatbot": "bot", "бот": "bot",
    "system": "system", "control": "system", "controlmessage": "system", "event": "system", "notification": "system",
    "система": "system",
}


def normalize_role(value: Any) -> str | None:
    """customer / end user -> client, agent / user -> operator, bot / application -> bot, control / system -> system."""
    text = _s(value)
    return _ROLE_ALIASES.get(text.lower()) if text else None


# --------------------------------------------------------------------------------------------- language


def detect_language(texts: Iterable[str]) -> str:
    """Kazakh-specific letters (әғқңөұүһі) -> KZ; other Cyrillic -> RU; otherwise EN."""
    cyr = kz = lat = 0
    for text in texts:
        for ch in text:
            if "Ѐ" <= ch <= "ӿ":
                cyr += 1
                if ch in KZ_LETTERS:
                    kz += 1
            elif ch.isascii() and ch.isalpha():
                lat += 1
    if cyr and cyr >= lat:
        return "KZ" if kz and kz / cyr >= KZ_MIN_SHARE else "RU"
    return "EN"


# --------------------------------------------------------------------------------------------- documentbody


_DATA_URI_RE = re.compile(r"^data:[^,]*;base64,", re.IGNORECASE)
_B64_RE = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")


def encode_documentbody(transcript: Any) -> str:
    """The inverse of decode_documentbody (UTF-8 JSON, standard base64): what Dataverse stores in annotation.documentbody."""
    return base64.b64encode(json.dumps(transcript, ensure_ascii=False).encode("utf-8")).decode("ascii")


def decode_documentbody(value: Any, *, field: str = "documentbody") -> Any:
    """base64 (standard or URL-safe, optional data: URI prefix, optional gzip) of UTF-8/UTF-16 JSON -> parsed JSON.

    Already decoded input (a list/dict, or a JSON text starting with '[' or '{') is returned parsed as is."""
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        raise DynamicsParseError("invalid_base64", f"Поле {field} должно быть строкой base64", field=field)
    text = value.strip()
    if not text:
        raise DynamicsParseError("empty_transcript", f"Поле {field} пустое", field=field)
    if len(text) > MAX_DOCUMENTBODY_CHARS:
        raise DynamicsParseError("payload_too_large", f"Поле {field} слишком большое (больше {MAX_DOCUMENTBODY_CHARS // 1_000_000} млн символов)",
                                 field=field, status=413)
    if text[0] in "[{":
        return _loads(text, field)
    text = re.sub(r"\s+", "", _DATA_URI_RE.sub("", text, count=1))
    if not _B64_RE.fullmatch(text):
        raise DynamicsParseError("invalid_base64", f"Поле {field} не является корректной строкой base64", field=field)
    if "-" in text or "_" in text:
        text = text.replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as e:
        raise DynamicsParseError("invalid_base64", f"Поле {field} не является корректной строкой base64", field=field) from e
    if raw[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
                raw = fh.read(MAX_DECODED_BYTES + 1)
        except (OSError, EOFError) as e:
            raise DynamicsParseError("invalid_transcript_json", f"Поле {field}: повреждённый gzip", field=field) from e
        if len(raw) > MAX_DECODED_BYTES:
            raise DynamicsParseError("payload_too_large", f"Поле {field}: распакованная стенограмма слишком большая", field=field, status=413)
    return _loads(_bytes_to_text(raw, field), field)


def _bytes_to_text(raw: bytes, field: str) -> str:
    error = DynamicsParseError("invalid_transcript_json", f"Поле {field}: содержимое не в кодировке UTF-8 или UTF-16", field=field)
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as e:
            raise error from e
    else:
        head = raw[:256]
        if len(head) >= 4:  # JSON keys and punctuation are ASCII: one zero byte per character in UTF-16
            odd_nul, even_nul = head[1::2].count(0), head[0::2].count(0)
            try:
                if odd_nul > len(head) // 4 and even_nul * 4 < odd_nul:
                    return raw.decode("utf-16-le")
                if even_nul > len(head) // 4 and odd_nul * 4 < even_nul:
                    return raw.decode("utf-16-be")
            except UnicodeDecodeError:
                pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise error from e


def _loads(text: str, field: str) -> Any:
    try:
        data = json.loads(text)
        if isinstance(data, str) and data.strip()[:1] in ("[", "{"):  # JSON encoded twice
            data = json.loads(data)
    except (json.JSONDecodeError, ValueError) as e:
        raise DynamicsParseError("invalid_transcript_json",
                                 f"Поле {field}: декодированное содержимое не является JSON-стенограммой Omnichannel", field=field) from e
    return data


# --------------------------------------------------------------------------------------------- locating the messages


_CONTEXT_KEYS = ("conversation", "liveworkitem", "msdyn_ocliveworkitem", "msdyn_transcript", "transcript_record")
_WRAPPER_KEYS = ("messages", "transcript", "value", "activities", "items", "conversation", "content", "body")


def _looks_like_message(d: dict[str, Any]) -> bool:
    return _ci(d, "content", "text") is not None and any(_ci(d, k) is not None for k in ("from", "createdDateTime", "created", "speaker", "role"))


def _reject_non_transcript(d: Any) -> None:
    otc = _s(_ci(d, "objecttypecode"))
    if not otc or otc.lower() in ("msdyn_transcript", "transcript"):
        return
    if otc.lower() == "msdyn_ocliveworkitem":
        raise DynamicsParseError("not_a_transcript", "Это вложение диалога (objecttypecode = msdyn_ocliveworkitem), а не стенограмма: "
                                 "передайте заметку с objecttypecode = msdyn_transcript", field="annotation.objecttypecode")
    raise DynamicsParseError("not_a_transcript", f"Заметка привязана к таблице «{otc}», а не к стенограмме (msdyn_transcript)",
                             field="annotation.objecttypecode")


def _from_decoded(obj: Any, label: str, contexts: list[dict[str, Any]], depth: int = 0) -> tuple[list[Any], str]:
    if isinstance(obj, str):
        if depth > 3:
            raise DynamicsParseError("invalid_transcript_json", f"Поле {label}: стенограмма не найдена", field=label)
        obj = decode_documentbody(obj, field=label)
    if isinstance(obj, list):
        return obj, label
    if isinstance(obj, dict):
        contexts.append(obj)
        for key in _WRAPPER_KEYS:
            value = _ci(obj, key)
            if isinstance(value, list):
                return value, f"{label}.{key}"
            if isinstance(value, (dict, str)) and depth < 3:
                try:
                    return _from_decoded(value, f"{label}.{key}", contexts, depth + 1)
                except DynamicsParseError:
                    continue
        if _looks_like_message(obj):
            return [obj], label
    raise DynamicsParseError("invalid_transcript_json", f"Поле {label}: не найден список сообщений Omnichannel", field=label)


def _locate(payload: Any) -> tuple[list[Any], str, list[dict[str, Any]]]:
    """Return (raw messages, the field they came from, context dicts with conversation metadata)."""
    contexts: list[dict[str, Any]] = []
    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8", errors="replace")
    if isinstance(payload, (str, list)):
        return (*_from_decoded(payload, "messages" if isinstance(payload, list) else "payload", contexts), contexts)
    if not isinstance(payload, dict):
        raise DynamicsParseError("missing_transcript", "Ожидается JSON-объект со стенограммой", field="body")
    contexts.append(payload)
    for key in _CONTEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            contexts.append(value)
    annotation = _ci(payload, "annotation")
    _reject_non_transcript(payload)
    if isinstance(annotation, dict):
        contexts.append(annotation)
        _reject_non_transcript(annotation)
    for name in ("messages", "transcript"):
        value = _ci(payload, name)
        if value is not None and value != "":
            return (*_from_decoded(value, name, contexts), contexts)
    body = _ci(payload, "documentbody")
    if body is not None:
        return (*_from_decoded(decode_documentbody(body, field="documentbody"), "documentbody", contexts), contexts)
    if isinstance(annotation, dict):
        body = _ci(annotation, "documentbody")
        if body is not None:
            label = "annotation.documentbody"
            return (*_from_decoded(decode_documentbody(body, field=label), label, contexts), contexts)
    elif isinstance(annotation, str) and annotation.strip():
        return (*_from_decoded(decode_documentbody(annotation, field="annotation"), "annotation", contexts), contexts)
    raise DynamicsParseError("missing_transcript", "Передайте стенограмму: annotation.documentbody, documentbody, transcript или messages",
                             field="annotation.documentbody")


# --------------------------------------------------------------------------------------------- one message


_FROM_KINDS = ("user", "application", "applicationInstance", "azureApplicationInstance", "bot", "guest", "phone", "device",
               "onPremises", "encrypted")
APP_KINDS = frozenset({"application", "applicationinstance", "azureapplicationinstance", "bot"})
CUSTOMER_KINDS = frozenset({"guest", "phone", "device"})
SYSTEM_APP_NAMES = frozenset({"dynamicsbot", "dynamics bot", "system", "omnichannel", "omnichannel system"})
GENERIC_AGENT_NAMES = frozenset({"agent", "агент", "operator", "оператор", "support", "поддержка", "служба поддержки",
                                 "customer service", "csr", "representative", "specialist", "специалист", "сотрудник"})
_CUSTOMER_NAME_RE = re.compile(r"^(?:customer|client|клиент|visitor|посетитель|guest|гость|anonymous|аноним|end ?user|тұтынушы)\b", re.IGNORECASE)
_CONTROL_TYPES = frozenset({"typing", "event", "conversationupdate", "endofconversation", "threadactivity"})
_TS_KEYS = ("createdDateTime", "created", "createdDateTimeUtc", "originalarrivaltime", "timestamp", "createdOn", "created_at",
            "createdAt", "time", "dateTime", "date")

_HTML_TAG_RE = re.compile(r"</?(?:p|div|span|br|b|i|u|s|strong|em|a|ul|ol|li|font|table|tbody|thead|tr|td|th|img|h[1-6]|"
                          r"blockquote|pre|code|sup|sub|hr)\b[^>]*>", re.IGNORECASE)
_ENTITY_RE = re.compile(r"&(?:[a-z]+|#\d+|#x[0-9a-f]+);", re.IGNORECASE)
_BREAK_RE = re.compile(r"<\s*(?:br\s*/?|/p|/div|/li|/tr|/h[1-6])\s*>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^<>]+>")
_WS_RE = re.compile(r"\s+")


def customer_like(name: str | None) -> bool:
    return bool(name and _CUSTOMER_NAME_RE.match(name.strip()))


def clean_text(text: str, content_type: str = "") -> str:
    """HTML (Teams/rich-text agents) -> plain text; literal '\\n' from double-escaped exports -> space; whitespace collapsed."""
    out = text
    if "html" in content_type.lower() or _HTML_TAG_RE.search(out) or _ENTITY_RE.search(out):
        out = _ANY_TAG_RE.sub(" ", _BREAK_RE.sub(" ", out))
        out = html.unescape(out)
    out = out.replace("\\r\\n", " ").replace("\\n", " ").replace("\\r", " ")
    out = _WS_RE.sub(" ", out).strip()
    if len(out) > MAX_TEXT_CHARS:
        out = out[:MAX_TEXT_CHARS].rstrip() + "…"
    return out


def _card_texts(obj: Any, depth: int = 0) -> list[str]:
    """Visible strings of an adaptive/hero card (text, title, subtitle)."""
    if depth > 8:
        return []
    if isinstance(obj, dict):
        out: list[str] = []
        for key, value in obj.items():
            if key in ("text", "title", "subtitle") and isinstance(value, str) and value.strip():
                out.append(value.strip())
            elif isinstance(value, (dict, list)):
                out += _card_texts(value, depth + 1)
        return out
    if isinstance(obj, list):
        return [t for item in obj for t in _card_texts(item, depth + 1)]
    return []


@dataclass
class _Msg:
    pos: int
    ts: str
    dt: datetime | None
    text: str
    raw: str
    files: list[str]
    tags: frozenset[str]
    control: bool
    system: bool
    kind: str | None
    sender_id: str | None
    sender_name: str | None
    explicit_role: str | None
    private: bool
    deleted: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind or "?", (self.sender_id or self.sender_name or "?").lower())


def _sender(m: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    frm = _ci(m, "from", "sender")
    if isinstance(frm, dict):
        for kind in _FROM_KINDS:
            ident = _ci(frm, kind)
            if isinstance(ident, dict) and (_s(_ci(ident, "id")) or _s(_ci(ident, "displayName", "name"))):
                return kind.lower(), _s(_ci(ident, "id")), _s(_ci(ident, "displayName", "name"))
        if _s(_ci(frm, "displayName", "name")) or _s(_ci(frm, "id")):
            return "user", _s(_ci(frm, "id")), _s(_ci(frm, "displayName", "name"))
        return None, None, None
    if isinstance(frm, str) and frm.strip():
        return "user", None, frm.strip()
    name, sid = _s(_ci(m, "fromDisplayName", "senderDisplayName", "senderName", "author")), _s(_ci(m, "fromId", "senderId", "authorId"))
    return ("user", sid, name) if (name or sid) else (None, None, None)


def _tags(m: dict[str, Any]) -> frozenset[str]:
    raw = _ci(m, "tags")
    items = raw if isinstance(raw, (list, tuple)) else (str(raw).split(",") if raw else [])
    return frozenset(str(t).strip().lower() for t in items if str(t).strip())


def _timestamp(m: dict[str, Any]) -> tuple[str, datetime | None]:
    for key in _TS_KEYS:
        value = _ci(m, key)
        if value is None or value == "" or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return _epoch(float(value))
        text = str(value).strip()
        if text.isdigit() and len(text) in (10, 13):
            return _epoch(float(text))
        return text, _aware(parse_iso(text))
    mid = _s(_ci(m, "id"))
    if mid and mid.isdigit() and len(mid) == 13:
        return _epoch(float(mid))
    return "", None


def _view(pos: int, m: dict[str, Any]) -> _Msg:
    kind, sender_id, sender_name = _sender(m)
    tags = _tags(m)
    mtype = str(_ci(m, "messageType", "type") or "").strip().lower()
    control = _truthy(_ci(m, "isControlMessage")) or "controlmessage" in tags or mtype.startswith("control") or mtype in _CONTROL_TYPES
    explicit_role = normalize_role(_ci(m, "speaker", "role", "participantRole"))
    raw = _ci(m, "content", "text", "message", "body")
    content_type = str(_ci(m, "contentType") or "")
    if isinstance(raw, dict):  # Graph-style body {contentType, content}
        content_type = content_type or str(_ci(raw, "contentType") or "")
        raw = _ci(raw, "content", "text")
    raw_text = "" if raw is None or isinstance(raw, (list, dict)) else str(raw)
    text = "" if control else clean_text(raw_text, content_type)
    if text[:1] in ("{", "[") and (kind in APP_KINDS or explicit_role == "bot" or "card" in content_type.lower() or "json" in content_type.lower()):
        try:
            card = json.loads(raw_text)
            text = clean_text(" ".join(_card_texts(card)))
        except ValueError:
            pass
    files: list[str] = []
    for att in _ci(m, "attachments") or []:
        if not isinstance(att, dict):
            continue
        name = _s(_ci(att, "name", "fileName", "filename"))
        card = _ci(att, "content")
        if name:
            files.append(name)
        elif isinstance(card, (dict, list)):
            extra = clean_text(" ".join(_card_texts(card)))
            if extra:
                text = f"{text} {extra}".strip()
        else:
            files.append("")
    system = explicit_role is None and not control and (
        "system" in tags or kind is None or (kind in APP_KINDS and (sender_name or "").strip().lower() in SYSTEM_APP_NAMES))
    visibility = str(_ci(m, "visibility") or "").strip().lower()
    private = "private" in tags or _truthy(_ci(m, "private")) or _truthy(_ci(m, "internal")) or visibility in ("private", "internal")
    ts, dt = _timestamp(m)
    return _Msg(pos=pos, ts=ts, dt=dt, text=text, raw=raw_text, files=files, tags=tags, control=control, system=system, kind=kind,
                sender_id=sender_id, sender_name=sender_name, explicit_role=explicit_role, private=private,
                deleted=_truthy(_ci(m, "deleted")))


# --------------------------------------------------------------------------------------------- order and roles


_MIN_DT = datetime.min.replace(tzinfo=UTC)


def _apply_order(msgs: list[_Msg], mode: str) -> tuple[list[_Msg], str]:
    if mode == "keep" or len(msgs) < 2:
        return msgs, "kept"
    dts = [m.dt for m in msgs if m.dt is not None]
    if mode == "auto":
        if len(dts) >= 2 and dts[0] > dts[-1] and all(a >= b for a, b in itertools.pairwise(dts)):
            return list(reversed(msgs)), "reversed"
        return msgs, "kept"
    first = dts[0] if dts else _MIN_DT
    keyed, last = [], None
    for m in msgs:
        last = m.dt or last
        keyed.append((last or first, m.pos, m))
    ordered = [m for _, _, m in sorted(keyed, key=lambda x: (x[0], x[1]))]
    changed = [m.pos for m in ordered] != [m.pos for m in msgs]
    return ordered, ("sorted" if changed else "kept")


_TARGET_RE = re.compile(r"<target>\s*([^<]+?)\s*</target>", re.IGNORECASE)


@dataclass
class _Roles:
    by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    operator_ids: set[str] = field(default_factory=set)
    client_ids: set[str] = field(default_factory=set)


@dataclass
class _Sender:
    kind: str | None
    sender_id: str | None
    name: str | None
    from_customer: bool = False
    public: bool = False


def _participant_overrides(participants: Any) -> dict[str, str]:
    if not participants:
        return {}
    if not isinstance(participants, dict):
        raise DynamicsParseError("invalid_participants", "participants: ожидается объект {\"имя или id\": \"client|operator|bot|system\"}",
                                 field="participants")
    out: dict[str, str] = {}
    for key, value in participants.items():
        role = normalize_role(value)
        if role is None:
            raise DynamicsParseError("invalid_participants", f"participants[{key}]: неизвестная роль «{value}»", field=f"participants.{key}")
        k = str(key).strip().lower()
        out[k] = role
        if ":" in k:
            out.setdefault(k.rsplit(":", 1)[-1], role)
    return out


def _classify(msgs: list[_Msg], overrides: dict[str, str]) -> _Roles:
    """Role per author. customer/end user -> client, agent/user -> operator, bot/application -> bot.

    Signals, strongest first: the caller's ``participants`` map; a customer-like display name ("Customer", "Клиент"...),
    the ``FromCustomer`` tag or a phone/guest identity -> client; an ``8:orgid:<id>`` target of an <addmember> control
    message or a ``public`` tag -> operator. Authors left without a signal default to operator, except that a transcript
    always has a customer (Microsoft: a transcript needs at least one customer message), so when no client was found the
    earliest unresolved author becomes the client."""
    agent_ids: set[str] = set()
    for m in msgs:
        if m.control:
            for target in _TARGET_RE.findall(m.raw):
                if "orgid:" in target.lower():
                    agent_ids.add(target.lower().rsplit(":", 1)[-1])
    senders: dict[tuple[str, str], _Sender] = {}
    for m in msgs:
        if m.control or m.system or m.explicit_role or m.deleted:
            continue
        s = senders.setdefault(m.key, _Sender(m.kind, m.sender_id, m.sender_name))
        s.from_customer = s.from_customer or "fromcustomer" in m.tags or "channelid-lcw" in m.tags
        s.public = s.public or "public" in m.tags
    roles = _Roles()
    unresolved: list[tuple[str, str]] = []
    explicit: set[tuple[str, str]] = set()
    for key, s in senders.items():
        sid = (s.sender_id or "").lower()
        override = overrides.get(sid) or overrides.get(sid.rsplit(":", 1)[-1] if sid else "") or overrides.get((s.name or "").strip().lower())
        if override:
            roles.by_key[key] = override
            explicit.add(key)
        elif s.kind in APP_KINDS:
            roles.by_key[key] = "client" if customer_like(s.name) else "bot"
        elif s.kind in CUSTOMER_KINDS or s.from_customer or customer_like(s.name):
            roles.by_key[key] = "client"
        elif (sid and sid.rsplit(":", 1)[-1] in agent_ids) or s.public:
            roles.by_key[key] = "operator"
        else:
            unresolved.append(key)
    if "client" not in roles.by_key.values():
        if unresolved:
            roles.by_key[unresolved.pop(0)] = "client"
        else:
            users = [k for k, s in senders.items() if s.kind not in APP_KINDS]
            candidates = [k for k in users if k not in explicit and (senders[k].sender_id or "").lower().rsplit(":", 1)[-1] not in agent_ids]
            if len(users) >= 2 and candidates:
                roles.by_key[candidates[0]] = "client"
    for key in unresolved:
        roles.by_key[key] = "operator"
    for key, role in roles.by_key.items():
        sid = (senders[key].sender_id or "").lower().rsplit(":", 1)[-1]
        if sid and role == "operator":
            roles.operator_ids.add(sid)
        elif sid and role == "client":
            roles.client_ids.add(sid)
    roles.operator_ids |= agent_ids - roles.client_ids
    return roles


# --------------------------------------------------------------------------------------------- rendering


CONTROL_TEXTS: dict[str, dict[str, str]] = {
    # RU/KZ/EN agent texts match AGENT_JOINED_RE in cqa.ingest.normalize (escalation anchor, call operator segment);
    # customer texts deliberately avoid the terminal-event phrases ("клиент покинул", "customer left").
    "RU": {"agent_joined": "Агент присоединился к беседе", "agent_left": "Агент вышел из беседы",
           "client_joined": "Клиент присоединился к беседе", "client_left": "Клиент вышел из беседы",
           "bot_joined": "Бот присоединился к беседе", "bot_left": "Бот вышел из беседы",
           "participant_joined": "Участник присоединился к беседе", "participant_left": "Участник вышел из беседы",
           "event": "Служебное событие", "file": "вложение"},
    "KZ": {"agent_joined": "Агент қосылды", "agent_left": "Агент әңгімеден шықты",
           "client_joined": "Клиент әңгімеге қосылды", "client_left": "Клиент әңгімеден шықты",
           "bot_joined": "Бот қосылды", "bot_left": "Бот әңгімеден шықты",
           "participant_joined": "Қатысушы қосылды", "participant_left": "Қатысушы әңгімеден шықты",
           "event": "Қызметтік оқиға", "file": "тіркеме"},
    "EN": {"agent_joined": "Agent joined the conversation", "agent_left": "Agent has left the conversation",
           "client_joined": "Customer joined the conversation", "client_left": "Customer has left the conversation",
           "bot_joined": "Bot joined the conversation", "bot_left": "Bot has left the conversation",
           "participant_joined": "Participant joined the conversation", "participant_left": "Participant has left the conversation",
           "event": "Service event", "file": "attachment"},
}


def _target_who(target: str, roles: _Roles) -> str:
    low = target.lower()
    tid = low.rsplit(":", 1)[-1]
    if "orgid:" in low or tid in roles.operator_ids:
        return "agent"
    if tid in roles.client_ids:
        return "client"
    if low.startswith("28:"):
        return "bot"
    return "participant"


def _control_text(m: _Msg, lang: str, mode: str, roles: _Roles) -> str | None:
    texts = CONTROL_TEXTS[lang]
    tag_match = re.search(r"<\s*([A-Za-z][\w-]*)", m.raw)
    tag = tag_match.group(1).lower() if tag_match else ""
    if tag in ("addmember", "deletemember"):
        action = "joined" if tag == "addmember" else "left"
        whos = [_target_who(t, roles) for t in _TARGET_RE.findall(m.raw)] or ["participant"]
        who = next(w for w in ("agent", "client", "bot", "participant") if w in whos)
        if mode == "events" and who not in ("agent", "client"):
            return None
        return texts[f"{who}_{action}"]
    if mode == "all":
        label = tag or (clean_text(m.raw)[:80] if m.raw else "control")
        return f"{texts['event']}: {label}"
    return None


def _with_files(text: str, files: list[str], lang: str) -> str:
    word = CONTROL_TEXTS[lang]["file"]
    labels = [f"[{word}: {name}]" if name else f"[{word}]" for name in files]
    return " ".join([text, *labels]).strip() if labels else text


@dataclass
class _Draft:
    msg: _Msg
    speaker: str
    kind: str  # text | control | internal | system


def _first_id(contexts: list[dict[str, Any]], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    for key in keys:
        for d in contexts:
            value = _ci(d, key)
            if value is None or value == "":
                continue
            cid = normalize_conversation_id(value, field=key)
            if cid:
                return cid, key
    return None, None


_LIVEWORKITEM_KEYS = ("msdyn_ocliveworkitemid", "_msdyn_liveworkitemidid_value", "msdyn_liveworkitemidid", "msdyn_liveworkitemid",
                      "liveworkitemid", "liveWorkItemId")
_TRANSCRIPT_KEYS = ("msdyn_transcriptid", "_objectid_value", "objectid")


def _detect_channel(contexts: list[dict[str, Any]], audio_url: str | None) -> tuple[str, str]:
    for d in contexts:
        for key in ("msdyn_channel", "msdyn_streamsource", "msdyn_channel@OData.Community.Display.V1.FormattedValue", "channel",
                    "channelType", "channel_type"):
            chan = normalize_channel(_ci(d, key))
            if chan:
                return chan, key
        target = str(_ci(d, "msdyn_transcripttargetidtype") or _ci(d, "_msdyn_transcripttarget_value@Microsoft.Dynamics.CRM.lookuplogicalname") or "")
        if target.lower() == "phonecall":
            return "voice call", "msdyn_transcripttarget"
        if _truthy(_ci(d, "isVoice", "is_voice")):
            return "voice call", "is_voice"
    if audio_url:
        return "voice call", "audio_url"
    return "agent chat", "default"


def parse_transcript(payload: Any, *, conversation_id: str | None = None, language: str | None = None, channel: str | None = None,
                     private_messages: str = "system", control_messages: str = "events", order: str = "auto",
                     participants: dict[str, str] | None = None, audio_url: str | None = None) -> dict[str, Any]:
    """Omnichannel transcript (annotation, documentbody, decoded JSON or message list) -> organizer record + "d365" stats.

    Speakers: customer/end user -> client, agent/user -> operator, bot/application -> bot, control and system messages ->
    system; private (agent-to-agent) messages -> system turns prefixed "[internal]" (private_messages="skip" drops them).
    Original order and timestamps are kept (ISO strings as sent; epoch ids become ISO); a list that is entirely newest-first
    is reversed (order="keep" disables that, order="chronological" sorts by time). Raises DynamicsParseError."""
    for name, value, allowed in (("private_messages", private_messages, PRIVATE_MODES), ("control_messages", control_messages, CONTROL_MODES),
                                 ("order", order, ORDER_MODES)):
        if value not in allowed:
            raise DynamicsParseError("invalid_option", f"{name}: допустимые значения {', '.join(allowed)}", field=name)
    overrides = _participant_overrides(participants)
    lang_hint = normalize_language(language)
    chan_hint = normalize_channel(channel, strict=True)
    explicit_id = normalize_conversation_id(conversation_id)

    raw_messages, source_field, contexts = _locate(payload)
    if len(raw_messages) > MAX_MESSAGES:
        raise DynamicsParseError("payload_too_large", f"Слишком много сообщений в стенограмме ({len(raw_messages)}, максимум {MAX_MESSAGES})",
                                 field=source_field, status=413)
    skipped: Counter[str] = Counter()
    msgs: list[_Msg] = []
    for pos, raw in enumerate(raw_messages):
        if isinstance(raw, dict):
            msgs.append(_view(pos, raw))
        else:
            skipped["invalid"] += 1
    msgs, order_applied = _apply_order(msgs, order)
    roles = _classify(msgs, overrides)

    drafts: list[_Draft] = []
    for m in msgs:
        if m.deleted:
            skipped["deleted"] += 1
        elif m.control:
            if control_messages == "none":
                skipped["control"] += 1
            else:
                drafts.append(_Draft(m, "system", "control"))
        elif not (m.text or m.files):
            skipped["empty"] += 1
        elif m.system:
            drafts.append(_Draft(m, "system", "system"))
        else:
            role = m.explicit_role or roles.by_key.get(m.key, "client")
            internal = role == "operator" and m.private
            drafts.append(_Draft(m, "system" if internal else role, "internal" if internal else "text"))
    private_ignored = False
    if any(d.kind == "internal" for d in drafts) and not any(d.speaker == "operator" for d in drafts):
        # every agent message is tagged private: the tag cannot mean "agent-to-agent" here, keep them as operator turns
        for d in drafts:
            if d.kind == "internal":
                d.speaker, d.kind = "operator", "text"
        private_ignored = True
    if private_messages == "skip":
        skipped["private"] += sum(1 for d in drafts if d.kind == "internal")
        drafts = [d for d in drafts if d.kind != "internal"]

    lang = lang_hint
    if lang is None:
        tiers = ([d.msg.text for d in drafts if d.kind == "text" and d.speaker in ("client", "operator")],
                 [d.msg.text for d in drafts if d.kind == "text" and d.speaker == "bot"],
                 [d.msg.text for d in drafts if d.speaker == "system"])
        lang = detect_language(next((t for t in tiers if _has_letters(t)), []))

    transcript: list[dict[str, str]] = []
    agent_counts: Counter[str] = Counter()
    internal_count = 0
    for d in drafts:
        m = d.msg
        if d.kind == "control":
            text = _control_text(m, lang, control_messages, roles)
            if not text:
                skipped["control"] += 1
                continue
        elif d.kind == "internal":
            internal_count += 1
            name = m.sender_name if m.sender_name and m.sender_name.strip().lower() not in GENERIC_AGENT_NAMES else None
            body = _with_files(m.text, m.files, lang)
            text = f"{INTERNAL_PREFIX} {name}: {body}" if name else f"{INTERNAL_PREFIX} {body}"
        else:
            text = _with_files(m.text, m.files, lang)
            if d.speaker == "operator" and m.sender_name and m.sender_name.strip().lower() not in GENERIC_AGENT_NAMES:
                agent_counts[m.sender_name.strip()] += 1
        transcript.append({"timestamp": m.ts, "speaker": d.speaker, "text": text})
    if not any(t["speaker"] in ("client", "operator", "bot") for t in transcript):
        raise DynamicsParseError("empty_transcript", "В стенограмме нет сообщений клиента, оператора или бота", field=source_field)

    if explicit_id:
        cid, cid_source = explicit_id, "conversation_id"
    else:
        cid, cid_source = _first_id(contexts, ("conversation_id", "conversationId", *_LIVEWORKITEM_KEYS))
        if cid is None:  # the payload (or its "conversation" object) is the msdyn_ocliveworkitem row itself
            cid, cid_source = _first_id(contexts, ("activityid",))
        if cid is None:
            cid, cid_source = _first_id(contexts, _TRANSCRIPT_KEYS)
        if cid is None:
            digest = hashlib.sha1(json.dumps(transcript, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            cid, cid_source = f"d365-{digest}", "content_hash"
    if chan_hint:
        chan, chan_source = chan_hint, "channel"
    else:
        chan, chan_source = _detect_channel(contexts, audio_url)

    liveworkitem_id, _ = _first_id(contexts, _LIVEWORKITEM_KEYS)
    transcript_id, _ = _first_id(contexts, _TRANSCRIPT_KEYS)
    annotation_id, _ = _first_id(contexts, ("annotationid",))
    stats = {
        "source_field": source_field,
        "messages_total": len(raw_messages),
        "turns": len(transcript),
        "skipped": dict(skipped),
        "internal_messages": internal_count,
        "private_ignored": private_ignored,
        "order": order_applied,
        "language_detected": lang_hint is None,
        "channel_source": chan_source,
        "conversation_id_source": cid_source,
        "liveworkitem_id": liveworkitem_id,
        "transcript_id": transcript_id,
        "annotation_id": annotation_id,
        "agents": [name for name, _ in agent_counts.most_common()],
        "speakers": dict(Counter(t["speaker"] for t in transcript)),
    }
    return {"conversation_id": cid, "channel": chan, "language": lang, "transcript": transcript, "audio_url": audio_url or None,
            "d365": stats}


def content_hash(record: dict[str, Any]) -> str:
    """Stable fingerprint of what the judge sees (channel, language, turns): unchanged re-posts do not re-evaluate."""
    basis = {"channel": record.get("channel"), "language": record.get("language"), "transcript": record.get("transcript")}
    return hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]


# --------------------------------------------------------------------------------------------- after the upsert


def enrich_conversation(conv: Any, stats: dict[str, Any], *, fingerprint: str | None = None) -> None:
    """Post-process a conversation that ``upsert_record`` just built from a D365 record.

    ``cqa.ingest.normalize`` parses chat timestamps as MM:SS only, so ISO chat timestamps leave every offset empty. Here
    chat offsets are filled from the ISO timestamps (seconds since the first timestamp), duplicates are re-checked with the
    same rule as the normalizer (same speaker and text within 2 s) and the first response is computed the normalizer's way.
    Calls are left alone (the normalizer already handles ISO for them). ``meta`` gets ``source`` and the parse statistics."""
    turns = sorted(conv.turns, key=lambda t: t.idx)
    if conv.channel != "voice call" and turns and all(t.offset_seconds is None for t in turns):
        base: datetime | None = None
        for t in turns:
            dt = _aware(parse_iso(t.timestamp_raw or "")) if t.timestamp_raw else None
            if dt is None:
                continue
            base = base or dt
            t.offset_seconds = round(max(0.0, (dt - base).total_seconds()), 3)
        prev = None
        for t in turns:
            close = prev is not None and (prev.offset_seconds is None or t.offset_seconds is None or abs(t.offset_seconds - prev.offset_seconds) <= 2.0)
            t.is_duplicate = bool(prev is not None and prev.speaker == t.speaker and prev.text == t.text and close)
            prev = t
        if conv.first_response_seconds is None:
            conv.first_response_seconds = _first_response(turns, conv.meta or {})
    meta = dict(conv.meta or {})
    meta["duplicates"] = sum(1 for t in turns if t.is_duplicate)
    meta["source"] = SOURCE
    meta["d365"] = {**stats, "content_hash": fingerprint, "ingested_at": datetime.now(UTC).isoformat(timespec="seconds")}
    conv.meta = meta


def _first_response(turns: list[Any], meta: dict[str, Any]) -> float | None:
    start = meta.get("operator_start_idx")
    first_op = next((t for t in turns if t.speaker == "operator" and (start is None or t.idx >= start)), None)
    if first_op is None or first_op.offset_seconds is None:
        return None
    esc = meta.get("escalation_idx")
    anchor = next((t for t in turns if t.idx == esc), None) if esc is not None else None
    if anchor is not None and anchor.offset_seconds is not None:
        return round(max(0.0, first_op.offset_seconds - anchor.offset_seconds), 3)
    return first_op.offset_seconds


# --------------------------------------------------------------------------------------------- sample (manifest, docs, tests)


def sample_messages() -> list[dict[str, Any]]:
    """A short synthetic Omnichannel transcript in the documented shape (no real people or data)."""
    agent = {"displayName": "Алина", "id": "5f1c9b7e-0000-4000-8000-00000000a001"}
    customer = {"displayName": "Customer", "id": "0c2d4e6f-0000-4000-8000-00000000c001"}
    bot = {"displayName": "Freedom Bot", "id": "28b0b0b0-0000-4000-8000-00000000b001"}
    def msg(ms: int, content: str, frm: dict[str, Any] | None, tags: str | None = None, **extra: Any) -> dict[str, Any]:
        created = datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="milliseconds")
        out: dict[str, Any] = {"id": str(ms), "content": content, "contentType": "text", "createdDateTime": created,
                               "created": created.replace("+00:00", "Z"), "attachments": [], "deleted": None, "from": frm}
        if tags is not None:
            out["tags"] = tags
        out.update(extra)
        return out
    t0 = 1_793_955_600_000  # 2026-11-06T09:00:00Z
    return [
        msg(t0, "Здравствуйте, не могу найти отчёт брокера за октябрь", {"user": customer}, "FromCustomer"),
        msg(t0 + 4_000, "Здравствуйте! Я виртуальный помощник Freedom. Соединяю со специалистом.", {"application": bot}, "public"),
        msg(t0 + 30_000, f"<addmember><eventtime>{t0 + 30_000}</eventtime><target>8:orgid:{agent['id']}</target></addmember>", None,
            isControlMessage=True),
        msg(t0 + 52_000, "Здравствуйте! Меня зовут Алина. Отчёт: «Меню → Отчёты и документы → Отчёт брокера», выберите период.",
            {"user": agent}, "public,messageId-1"),
        msg(t0 + 80_000, "Нашёл, спасибо!", {"user": customer}, "FromCustomer"),
        msg(t0 + 95_000, "Рада помочь! Остались ли вопросы? Хорошего дня!", {"user": agent}, "public,messageId-2"),
        msg(t0 + 120_000, "Алина has left the conversation.", {"application": {"displayName": "DynamicsBot", "id": "bc81db89"}}, "system"),
    ]
