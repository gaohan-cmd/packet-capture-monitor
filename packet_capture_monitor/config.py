from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import urlsplit, urlunsplit


DEFAULT_TARGET = "https://www.aliyundrive.com/"


def configured_default_target() -> str:
    return os.environ.get("MONITOR_TARGET_URL") or os.environ.get("MONITOR_TARGET_HOST") or DEFAULT_TARGET


def normalize_target_url(value: str) -> Dict[str, Any]:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("target url is required")
    if "://" not in raw:
        raw = "https://" + raw

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("target url must use http or https")

    host = (parts.hostname or "").lower().strip(".")
    if not host or " " in host:
        raise ValueError("target url host is invalid")

    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"

    path = parts.path or "/"
    normalized_path = path if path.startswith("/") else "/" + path
    normalized_url = urlunsplit((scheme, netloc, normalized_path.rstrip("/") or "/", "", ""))

    return {
        "target_url": normalized_url,
        "target_scheme": scheme,
        "target_host": host,
        "target_port": parts.port,
        "target_path": normalized_path.rstrip("/") or "/",
    }
