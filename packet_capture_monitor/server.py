from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import configured_default_target, normalize_target_url
from .store import CaptureStore
from .users import UserAccount, UserDirectory


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
MONITOR_TOKEN = os.environ.get("MONITOR_TOKEN", "")
SESSION_SECRET = os.environ.get("MONITOR_SESSION_SECRET") or os.environ.get("MONITOR_TOKEN") or secrets.token_urlsafe(32)
SESSION_COOKIE = "packet_monitor_session"
SESSION_TTL_SECONDS = int(os.environ.get("DASHBOARD_SESSION_TTL", str(12 * 60 * 60)))
COOKIE_SECURE = os.environ.get("DASHBOARD_COOKIE_SECURE", "0") == "1"


app = FastAPI(title="Packet Capture Monitor", version="0.1.0")
users = UserDirectory.from_env()
store = CaptureStore()
store.migrate_legacy_user_id(users.default_user_id)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        connections = self.active_connections.get(user_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.active_connections.pop(user_id, None)

    async def broadcast(self, user_id: str, message: Dict[str, Any]) -> None:
        stale: List[WebSocket] = []
        for connection in list(self.active_connections.get(user_id, [])):
            try:
                await connection.send_json(message)
            except RuntimeError:
                stale.append(connection)
        for connection in stale:
            self.disconnect(user_id, connection)


manager = ConnectionManager()


def verify_token(header_token: str) -> None:
    if MONITOR_TOKEN and not hmac.compare_digest(header_token, MONITOR_TOKEN):
        raise HTTPException(status_code=401, detail="invalid capture token")


def token_is_valid(header_token: str) -> bool:
    return bool(MONITOR_TOKEN and hmac.compare_digest(header_token, MONITOR_TOKEN))


def auth_enabled() -> bool:
    return users.auth_enabled()


def sign_session(message: str) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_value(user_id: str) -> str:
    expires_at = str(int(time.time()) + SESSION_TTL_SECONDS)
    message = f"{user_id}:{expires_at}"
    signed = f"{message}:{sign_session(message)}"
    return base64.urlsafe_b64encode(signed.encode("utf-8")).decode("ascii")


def session_user_id(value: str | None) -> str | None:
    if not auth_enabled():
        return users.default_user_id
    if not value:
        return None
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        user_id, expires_at, signature = decoded.rsplit(":", 2)
    except (ValueError, UnicodeDecodeError, TypeError):
        return None
    message = f"{user_id}:{expires_at}"
    if not hmac.compare_digest(signature, sign_session(message)):
        return None
    try:
        expired = int(expires_at) < int(time.time())
    except ValueError:
        return None
    if expired:
        return None
    if not users.get(user_id):
        return None
    return user_id


def valid_session(value: str | None) -> bool:
    return session_user_id(value) is not None


def request_has_dashboard_access(request: Request) -> bool:
    return session_user_id(request.cookies.get(SESSION_COOKIE)) is not None


def require_dashboard_user(request: Request) -> UserAccount:
    user_id = session_user_id(request.cookies.get(SESSION_COOKIE))
    if not user_id:
        raise HTTPException(status_code=401, detail="login required")
    account = users.get(user_id)
    if not account:
        raise HTTPException(status_code=401, detail="login required")
    return account


def current_config(account: UserAccount) -> Dict[str, Any]:
    target_url = store.get_setting(
        account.user_id,
        "target_url",
        account.default_target_url or configured_default_target(),
    )
    target = normalize_target_url(target_url)
    include_subdomains = store.get_setting(
        account.user_id,
        "include_subdomains",
        account.include_subdomains,
    )
    return {
        **target,
        "include_subdomains": bool(include_subdomains),
        "dashboard": "Packet Capture Monitor",
        "user": users.public_user(account),
        "sensitive_headers": ["authorization", "cookie", "set-cookie", "proxy-authorization"],
    }


@app.get("/")
async def index(request: Request) -> Response:
    if not request_has_dashboard_access(request):
        return RedirectResponse("/login")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
async def login_page() -> Response:
    if not auth_enabled():
        return RedirectResponse("/")
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/api/login")
async def login(payload: Dict[str, Any] = Body(...)) -> Response:
    if not auth_enabled():
        return JSONResponse({"ok": True})
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    account = users.authenticate_dashboard(username, password)
    if not account:
        raise HTTPException(status_code=401, detail="invalid username or password")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        make_session_value(account.user_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    return response


@app.post("/api/logout")
async def logout() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/config")
async def config(request: Request, user_id: str = "") -> Dict[str, Any]:
    if token_is_valid(request.headers.get("x-capture-token", "")):
        account = users.get(user_id or users.default_user_id)
        if not account:
            raise HTTPException(status_code=404, detail="user not found")
        return current_config(account)
    return current_config(require_dashboard_user(request))


@app.put("/api/config")
async def update_config(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    account = require_dashboard_user(request)
    try:
        target = normalize_target_url(str(payload.get("target_url") or payload.get("target_host") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.set_setting(account.user_id, "target_url", target["target_url"])
    if "include_subdomains" in payload:
        store.set_setting(account.user_id, "include_subdomains", bool(payload["include_subdomains"]))

    updated = current_config(account)
    await manager.broadcast(account.user_id, {"type": "config", "config": updated})
    return updated


@app.post("/api/proxy-auth")
async def proxy_auth(
    payload: Dict[str, Any] = Body(...),
    x_capture_token: str = Header(default=""),
) -> Dict[str, Any]:
    verify_token(x_capture_token)
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    account = users.authenticate_proxy(username, password)
    if not account:
        raise HTTPException(status_code=401, detail="invalid proxy username or password")
    return {
        "ok": True,
        "user_id": account.user_id,
        "config": current_config(account),
    }


@app.get("/api/captures")
async def list_captures(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    q: str = "",
    method: str = "",
    status: str = "",
) -> Dict[str, Any]:
    account = require_dashboard_user(request)
    return {
        "items": store.list(
            account.user_id,
            limit=limit,
            query=q.strip(),
            method=method.strip(),
            status=status.strip(),
        ),
        "stats": store.stats(account.user_id),
    }


@app.get("/api/captures/{capture_id}")
async def get_capture(request: Request, capture_id: str) -> Dict[str, Any]:
    account = require_dashboard_user(request)
    capture = store.get(account.user_id, capture_id)
    if not capture:
        raise HTTPException(status_code=404, detail="capture not found")
    return capture


@app.post("/api/captures/events")
async def capture_event(
    payload: Dict[str, Any] = Body(...),
    x_capture_token: str = Header(default=""),
) -> Dict[str, Any]:
    verify_token(x_capture_token)
    user_id = str(payload.get("user_id") or "")
    account = users.get(user_id)
    if not account:
        raise HTTPException(status_code=404, detail="user not found")
    try:
        record = store.upsert_event(account.user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = store.summarize(record)
    await manager.broadcast(
        account.user_id,
        {"type": "capture", "item": summary, "stats": store.stats(account.user_id)},
    )
    return {"ok": True, "item": summary}


@app.delete("/api/captures")
async def clear_captures(request: Request) -> Dict[str, Any]:
    account = require_dashboard_user(request)
    store.clear(account.user_id)
    await manager.broadcast(account.user_id, {"type": "clear", "stats": store.stats(account.user_id)})
    return {"ok": True}


@app.websocket("/ws/captures")
async def captures_ws(websocket: WebSocket) -> None:
    user_id = session_user_id(websocket.cookies.get(SESSION_COOKIE))
    if not user_id:
        await websocket.close(code=1008)
        return
    await manager.connect(user_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "snapshot",
                "items": store.list(user_id, limit=200),
                "stats": store.stats(user_id),
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
