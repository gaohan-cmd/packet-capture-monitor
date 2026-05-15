from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import request as urlrequest
from urllib.error import URLError
from urllib.parse import urlsplit


TEXTUAL_CONTENT_HINTS = (
    "text/",
    "application/json",
    "application/javascript",
    "application/x-javascript",
    "application/xml",
    "application/xhtml+xml",
    "application/x-www-form-urlencoded",
    "application/graphql",
    "application/problem+json",
    "application/ld+json",
    "application/manifest+json",
    "+json",
    "+xml",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_content_type(headers: Iterable[Tuple[str, str]]) -> str:
    for name, value in headers:
        if name.lower() == "content-type":
            return value
    return ""


def charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip("\"'") or "utf-8"
    return "utf-8"


def is_textual(content_type: str) -> bool:
    lowered = content_type.lower()
    return any(hint in lowered for hint in TEXTUAL_CONTENT_HINTS)


def encode_body(raw: Optional[bytes], content_type: str, limit: int) -> Dict[str, Any]:
    if not raw:
        return {
            "kind": "empty",
            "content_type": content_type,
            "size": 0,
            "truncated": False,
        }

    size = len(raw)
    clipped = raw[:limit]
    truncated = size > limit
    lowered_type = content_type.lower()
    body: Dict[str, Any] = {
        "kind": "binary",
        "content_type": content_type,
        "size": size,
        "truncated": truncated,
    }

    if "image/" in lowered_type:
        body["kind"] = "image"
        body["base64"] = base64.b64encode(clipped).decode("ascii")
        return body

    if is_textual(content_type):
        charset = charset_from_content_type(content_type)
        text = clipped.decode(charset, errors="replace")
        stripped = text.lstrip()
        body["kind"] = "json" if stripped.startswith(("{", "[")) else "text"
        body["text"] = text
        return body

    body["base64"] = base64.b64encode(clipped).decode("ascii")
    body["hex"] = clipped[:512].hex(" ")
    return body


def headers_to_pairs(headers: Any) -> List[Dict[str, str]]:
    try:
        pairs = list(headers.items(multi=True))
    except TypeError:
        pairs = list(headers.items())
    return [{"name": str(name), "value": str(value)} for name, value in pairs]


class DomainCaptureAddon:
    def __init__(self) -> None:
        self.api_url = os.environ.get("MONITOR_API_URL", "http://127.0.0.1:8765").rstrip("/")
        configured_target = os.environ.get("MONITOR_TARGET_URL") or os.environ.get(
            "MONITOR_TARGET_HOST", "ikuuu.win"
        )
        self.target_scheme, self.target_host, self.target_path = self.parse_target(configured_target)
        self.include_subdomains = os.environ.get("MONITOR_INCLUDE_SUBDOMAINS", "1") != "0"
        self.body_limit = int(os.environ.get("MONITOR_BODY_LIMIT", str(2 * 1024 * 1024)))
        self.token = os.environ.get("MONITOR_TOKEN", "")
        self._config_checked_at = 0.0

    @staticmethod
    def parse_target(value: str) -> Tuple[str, str, str]:
        raw = (value or "").strip()
        if "://" not in raw:
            raw = "https://" + raw
        parts = urlsplit(raw)
        scheme = parts.scheme.lower() if parts.scheme.lower() in {"http", "https"} else "https"
        host = (parts.hostname or raw).lower().strip(".")
        path = (parts.path or "/").rstrip("/") or "/"
        return scheme, host, path

    def refresh_config(self) -> None:
        now = time.time()
        if now - self._config_checked_at < 2:
            return
        self._config_checked_at = now
        req = urlrequest.Request(
            self.api_url + "/api/config",
            headers={"X-Capture-Token": self.token},
            method="GET",
        )
        try:
            with urlrequest.urlopen(req, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, json.JSONDecodeError):
            return

        target_url = payload.get("target_url") or payload.get("target_host")
        if target_url:
            self.target_scheme, self.target_host, self.target_path = self.parse_target(str(target_url))
        if "include_subdomains" in payload:
            self.include_subdomains = bool(payload["include_subdomains"])

    def matches(self, host: str, scheme: str, path: str) -> bool:
        self.refresh_config()
        normalized = host.lower().strip(".")
        request_path = urlsplit(path).path or "/"
        if scheme.lower() != self.target_scheme:
            return False
        host_matches = normalized == self.target_host or (
            self.include_subdomains and normalized.endswith("." + self.target_host)
        )
        if not host_matches:
            return False
        if self.target_path == "/":
            return True
        return request_path == self.target_path or request_path.startswith(self.target_path + "/")

    def post_event(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urlrequest.Request(
            self.api_url + "/api/captures/events",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Capture-Token": self.token,
            },
            method="POST",
        )
        try:
            urlrequest.urlopen(req, timeout=2).read()
        except URLError as exc:
            print(f"[packet-monitor] dashboard event delivery failed: {exc}")

    def request(self, flow: Any) -> None:
        if not self.matches(flow.request.pretty_host, flow.request.scheme, flow.request.path):
            return

        capture_id = str(uuid.uuid4())
        flow.metadata["packet_monitor_capture_id"] = capture_id
        flow.metadata["packet_monitor_started_at"] = time.time()

        headers = headers_to_pairs(flow.request.headers)
        content_type = parse_content_type((item["name"], item["value"]) for item in headers)
        body = encode_body(flow.request.get_content(strict=False), content_type, self.body_limit)

        self.post_event(
            {
                "type": "request",
                "capture_id": capture_id,
                "timestamp": now_iso(),
                "request": {
                    "method": flow.request.method,
                    "scheme": flow.request.scheme,
                    "host": flow.request.pretty_host,
                    "path": flow.request.path,
                    "url": flow.request.pretty_url,
                    "http_version": flow.request.http_version,
                    "headers": headers,
                    "body": body,
                },
            }
        )

    def response(self, flow: Any) -> None:
        capture_id = flow.metadata.get("packet_monitor_capture_id")
        if not capture_id:
            if not self.matches(flow.request.pretty_host, flow.request.scheme, flow.request.path):
                return
            capture_id = str(uuid.uuid4())

        headers = headers_to_pairs(flow.response.headers)
        content_type = parse_content_type((item["name"], item["value"]) for item in headers)
        body = encode_body(flow.response.get_content(strict=False), content_type, self.body_limit)
        started_at = flow.metadata.get("packet_monitor_started_at")
        duration_ms = None
        if started_at:
            duration_ms = round((time.time() - float(started_at)) * 1000, 2)

        self.post_event(
            {
                "type": "response",
                "capture_id": capture_id,
                "timestamp": now_iso(),
                "response": {
                    "status_code": flow.response.status_code,
                    "reason": flow.response.reason,
                    "headers": headers,
                    "body": body,
                    "duration_ms": duration_ms,
                },
            }
        )

    def error(self, flow: Any) -> None:
        capture_id = flow.metadata.get("packet_monitor_capture_id")
        if not capture_id:
            return
        self.post_event(
            {
                "type": "error",
                "capture_id": capture_id,
                "timestamp": now_iso(),
                "error": str(flow.error),
            }
        )


addons = [DomainCaptureAddon()]
