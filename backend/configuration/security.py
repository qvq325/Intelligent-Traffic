"""Credential redaction helpers shared by audit and API presentation."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_QUERY_PARTS = ("token", "password", "passwd", "secret", "key", "auth", "signature")
_RTSP_URL = re.compile(r"rtsps?://[^\s\"'<>]+", re.IGNORECASE)


def redact_url_userinfo(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[redacted-url]"
    if not parsed.scheme or not parsed.netloc or "@" not in parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    query = urlencode(
        [
            (
                key,
                "***" if any(part in key.lower() for part in _SENSITIVE_QUERY_PARTS) else item,
            )
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    netloc = f"***:***@{hostname}{port}" if "@" in parsed.netloc else parsed.netloc
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def redact_url_credentials(value: str) -> str:
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError:
        return "[redacted-url]"
    if not parsed.scheme or not parsed.netloc:
        return "[redacted-url]"
    netloc = parsed.netloc
    if "@" in parsed.netloc:
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        port = f":{parsed_port}" if parsed_port is not None else ""
        netloc = f"***:***@{hostname}{port}"
    query = urlencode(
        [
            (
                key,
                "***" if any(part in key.lower() for part in _SENSITIVE_QUERY_PARTS) else item,
            )
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def redact_text(value: str) -> str:
    return _RTSP_URL.sub(lambda match: redact_url_userinfo(match.group(0)), value)


def redact_mapping(value):
    if isinstance(value, dict):
        return {
            key: redact_url_userinfo(item) if key == "rtsp_url" and isinstance(item, str) else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value
