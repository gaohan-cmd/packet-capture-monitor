from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit


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
        target_scheme, target_host, target_path = self.parse_target(configured_target)
        self.default_user_id = os.environ.get("MONITOR_DEFAULT_USER_ID", "admin")
        self.default_config = {
            "target_scheme": target_scheme,
            "target_host": target_host,
            "target_path": target_path,
            "include_subdomains": os.environ.get("MONITOR_INCLUDE_SUBDOMAINS", "1") != "0",
            "checked_at": 0.0,
        }
        self.user_configs: Dict[str, Dict[str, Any]] = {}
        self.credential_cache: Dict[str, Tuple[float, Optional[str]]] = {}
        self.proxy_auth_required = os.environ.get("MONITOR_PROXY_AUTH_REQUIRED", "0") == "1"
        self.body_limit = int(os.environ.get("MONITOR_BODY_LIMIT", str(2 * 1024 * 1024)))
        self.token = os.environ.get("MONITOR_TOKEN", "")

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

    def config_for_user(self, user_id: str) -> Dict[str, Any]:
        if user_id not in self.user_configs:
            self.user_configs[user_id] = dict(self.default_config)
        self.refresh_config(user_id)
        return self.user_configs[user_id]

    def refresh_config(self, user_id: str) -> None:
        config = self.user_configs.setdefault(user_id, dict(self.default_config))
        now = time.time()
        if now - float(config.get("checked_at") or 0) < 2:
            return
        config["checked_at"] = now
        path = "/api/config"
        if user_id:
            path += "?user_id=" + quote(user_id)
        req = urlrequest.Request(
            self.api_url + path,
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
            (
                config["target_scheme"],
                config["target_host"],
                config["target_path"],
            ) = self.parse_target(str(target_url))
        if "include_subdomains" in payload:
            config["include_subdomains"] = bool(payload["include_subdomains"])

    @staticmethod
    def parse_basic_auth(value: str) -> Optional[Tuple[str, str]]:
        if not value.lower().startswith("basic "):
            return None
        try:
            decoded = base64.b64decode(value.split(" ", 1)[1]).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None
        return username, password

    def credentials_from_flow(self, flow: Any) -> Optional[Tuple[str, str]]:
        proxyauth = flow.metadata.get("proxyauth")
        if isinstance(proxyauth, (tuple, list)) and len(proxyauth) == 2:
            return str(proxyauth[0]), str(proxyauth[1])
        if isinstance(proxyauth, str) and ":" in proxyauth:
            username, password = proxyauth.split(":", 1)
            return username, password

        header_value = flow.request.headers.get("Proxy-Authorization", "")
        return self.parse_basic_auth(header_value)

    def reject_proxy_auth(self, flow: Any) -> None:
        from mitmproxy import http

        flow.response = http.Response.make(
            407,
            b"Proxy Authentication Required",
            {"Proxy-Authenticate": 'Basic realm="Packet Capture Monitor"'},
        )

    def authenticate_proxy_user(self, username: str, password: str) -> Optional[str]:
        cache_key = username + "\0" + password
        cached = self.credential_cache.get(cache_key)
        now = time.time()
        if cached and cached[0] > now:
            return cached[1]

        data = json.dumps({"username": username, "password": password}).encode("utf-8")
        req = urlrequest.Request(
            self.api_url + "/api/proxy-auth",
            data=data,
            headers={
                "Content-Type": "application/json",
                "X-Capture-Token": self.token,
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403, 404}:
                self.credential_cache[cache_key] = (now + 15, None)
                return None
            print(f"[packet-monitor] proxy auth check failed: {exc}")
            return None
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"[packet-monitor] proxy auth check failed: {exc}")
            return None

        user_id = str(payload.get("user_id") or "")
        if not user_id:
            return None
        config_payload = payload.get("config")
        if isinstance(config_payload, dict):
            self.apply_config_payload(user_id, config_payload)
        self.credential_cache[cache_key] = (now + 30, user_id)
        return user_id

    def apply_config_payload(self, user_id: str, payload: Dict[str, Any]) -> None:
        config = self.user_configs.setdefault(user_id, dict(self.default_config))
        target_url = payload.get("target_url") or payload.get("target_host")
        if target_url:
            (
                config["target_scheme"],
                config["target_host"],
                config["target_path"],
            ) = self.parse_target(str(target_url))
        if "include_subdomains" in payload:
            config["include_subdomains"] = bool(payload["include_subdomains"])
        config["checked_at"] = time.time()

    def user_id_for_flow(self, flow: Any) -> Optional[str]:
        cached = flow.metadata.get("packet_monitor_user_id")
        if cached:
            return str(cached)
        credentials = self.credentials_from_flow(flow)
        if not credentials:
            if self.proxy_auth_required:
                self.reject_proxy_auth(flow)
                return None
            flow.metadata["packet_monitor_user_id"] = self.default_user_id
            return self.default_user_id

        user_id = self.authenticate_proxy_user(credentials[0], credentials[1])
        if not user_id:
            self.reject_proxy_auth(flow)
            return None
        flow.metadata["packet_monitor_user_id"] = user_id
        return user_id

    def matches(self, user_id: str, host: str, scheme: str, path: str) -> bool:
        config = self.config_for_user(user_id)
        normalized = host.lower().strip(".")
        request_path = urlsplit(path).path or "/"
        if scheme.lower() != config["target_scheme"]:
            return False
        host_matches = normalized == config["target_host"] or (
            config["include_subdomains"] and normalized.endswith("." + config["target_host"])
        )
        if not host_matches:
            return False
        if config["target_path"] == "/":
            return True
        return request_path == config["target_path"] or request_path.startswith(config["target_path"] + "/")

    def post_event(self, user_id: str, payload: Dict[str, Any]) -> None:
        payload["user_id"] = user_id
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

    def http_connect(self, flow: Any) -> None:
        self.user_id_for_flow(flow)

    def request(self, flow: Any) -> None:
        user_id = self.user_id_for_flow(flow)
        if not user_id:
            return
        if not self.matches(user_id, flow.request.pretty_host, flow.request.scheme, flow.request.path):
            return

        capture_id = str(uuid.uuid4())
        flow.metadata["packet_monitor_capture_id"] = capture_id
        flow.metadata["packet_monitor_user_id"] = user_id
        flow.metadata["packet_monitor_started_at"] = time.time()

        headers = headers_to_pairs(flow.request.headers)
        content_type = parse_content_type((item["name"], item["value"]) for item in headers)
        body = encode_body(flow.request.get_content(strict=False), content_type, self.body_limit)

        self.post_event(
            user_id,
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
        user_id = flow.metadata.get("packet_monitor_user_id")
        if not user_id:
            user_id = self.user_id_for_flow(flow)
        if not user_id:
            return
        capture_id = flow.metadata.get("packet_monitor_capture_id")
        if not capture_id:
            if not self.matches(str(user_id), flow.request.pretty_host, flow.request.scheme, flow.request.path):
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
            str(user_id),
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
        user_id = flow.metadata.get("packet_monitor_user_id")
        if not user_id:
            return
        capture_id = flow.metadata.get("packet_monitor_capture_id")
        if not capture_id:
            return
        self.post_event(
            str(user_id),
            {
                "type": "error",
                "capture_id": capture_id,
                "timestamp": now_iso(),
                "error": str(flow.error),
            }
        )


addons = [DomainCaptureAddon()]
