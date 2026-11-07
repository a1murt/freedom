"""Which OpenAI-compatible endpoints the local judge, the health probe and PUT /api/settings may use.

The local provider receives transcripts without PII masking and may carry ``LOCAL_API_KEY``, and GET /api/system/health
probes the endpoint for every visitor, so a URL stored through the settings API must not point at arbitrary hosts
(server-side request forgery, credential and transcript exfiltration).

Rules (``check_endpoint``):
- the endpoint configured in the environment (``LOCAL_ENDPOINT``) is always accepted and trusted;
- entries of ``LOCAL_ENDPOINT_ALLOWLIST`` (comma-separated ``scheme://host[:port][/path]``) are accepted and trusted:
  same scheme, host and port, and the path must start with the entry's path;
- off the public hosted instance (a laptop running Ollama, a GPU box on the LAN, a docker-compose service) local-network
  hosts are accepted too, but not trusted: loopback and private address literals (127.0.0.0/8, ::1, 10/8, 172.16/12,
  192.168/16, fc00::/7), ``localhost``, single-label names (``ollama``) and names under .local, .lan, .localhost,
  .home.arpa;
- everything else is refused: public hosts, link-local and cloud metadata addresses (169.254/16, fe80::/10,
  ``*.internal``), the unspecified and multicast ranges, non-http(s) schemes.

The hosted instance is detected from the Railway environment (settings.is_public_instance); set CQA_PUBLIC_INSTANCE=1 on
any other internet-facing deployment.

``LOCAL_API_KEY`` goes only to trusted endpoints; any other accepted endpoint gets ``Bearer none``. A stored value that
is no longer accepted (e.g. saved before these rules) is ignored at run time: the environment endpoint is used instead.
"""
from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from ..config import settings

log = logging.getLogger("cqa.judge.local")

_PRIVATE_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
_WARNED: set[str] = set()


def _parts(url: str | None) -> tuple[str, str, int, str] | None:
    """(scheme, host, port, path) of an http(s) URL without credentials, else None."""
    text = (url or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port, (parts.path or "").rstrip("/")


def normalize(url: str | None) -> str | None:
    text = (url or "").strip()
    return text.rstrip("/") or None


def env_endpoint() -> str | None:
    return normalize(settings.local_endpoint)


def allowlist() -> list[str]:
    raw = settings.local_endpoint_allowlist or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def _matches(url: str, entry: str) -> bool:
    a, b = _parts(url), _parts(entry)
    if a is None or b is None:
        return False
    return a[:3] == b[:3] and (not b[3] or a[3] == b[3] or a[3].startswith(b[3] + "/"))


def trusted(url: str | None) -> bool:
    """The environment endpoint or an allowlisted one: these may receive LOCAL_API_KEY."""
    if not url or _parts(url) is None:
        return False
    env = env_endpoint()
    if env and _parts(env) is not None and _matches(url, env) and _parts(url)[3] == _parts(env)[3]:
        return True
    return any(_matches(url, entry) for entry in allowlist())


_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa")


def _local_network(host: str) -> bool:
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return "." not in host  # a single-label name (docker-compose service, LAN host); never *.internal
    if ip.is_loopback:
        return True
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    return any(ip in net for net in _PRIVATE_NETS)


def check_endpoint(url: str | None) -> tuple[bool, str]:
    """(accepted, reason in Russian when refused)."""
    parts = _parts(url)
    if parts is None:
        return False, "Ожидается адрес вида http(s)://хост[:порт]/путь."
    if trusted(url):
        return True, ""
    if not settings.is_public_instance() and _local_network(parts[1]):
        return True, ""
    if settings.is_public_instance():
        return False, ("На публичном сервере адрес локальной модели задаётся только переменной окружения LOCAL_ENDPOINT "
                       "или списком LOCAL_ENDPOINT_ALLOWLIST.")
    return False, ("Допустимы только адреса локальной сети (localhost, 127.0.0.1, 10.x, 172.16–31.x, 192.168.x) или адреса из "
                   "LOCAL_ENDPOINT_ALLOWLIST; служебные адреса вроде 169.254.169.254 запрещены.")


def effective_endpoint(stored: str | None) -> str | None:
    """The endpoint the local judge uses: an accepted stored value (PUT /api/settings), else LOCAL_ENDPOINT."""
    value = normalize(stored)
    if value and value != env_endpoint():
        ok, _ = check_endpoint(value)
        if ok:
            return value
        if value not in _WARNED:
            _WARNED.add(value)
            log.warning("stored local_endpoint %r is not allowed; using LOCAL_ENDPOINT instead", value)
        return env_endpoint()
    return value or env_endpoint()


def api_key_for(url: str | None) -> str:
    """LOCAL_API_KEY for trusted endpoints, the placeholder "none" for any other one."""
    return settings.local_api_key if trusted(url) else "none"
