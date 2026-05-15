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


PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
MONITOR_TOKEN = os.environ.get("MONITOR_TOKEN", "")
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
SESSION_SECRET = os.environ.get("MONITOR_SESSION_SECRET") or os.environ.get("MONITOR_TOKEN") or secrets.token_urlsafe(32)
SESSION_COOKIE = "packet_monitor_session"
SESSION_TTL_SECONDS = int(os.environ.get("DASHBOARD_SESSION_TTL", str(12 * 60 * 60)))
COOKIE_SECURE = os.environ.get("DASHBOARD_COOKIE_SECURE", "0") == "1"


app = FastAPI(title="Packet Capture Monitor", version="0.1.0")
store = CaptureStore()


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        stale: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except RuntimeError:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


manager = ConnectionManager()


def verify_token(header_token: str) -> None:
    if MONITOR_TOKEN and header_token != MONITOR_TOKEN:
        raise HTTPException(status_code=401, detail="invalid capture token")


def token_is_valid(header_token: str) -> bool:
    return bool(MONITOR_TOKEN and hmac.compare_digest(header_token, MONITOR_TOKEN))


def auth_enabled() -> bool:
    return bool(DASHBOARD_PASSWORD)


def sign_session(message: str) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_value(username: str) -> str:
    expires_at = str(int(time.time()) + SESSION_TTL_SECONDS)
    message = f"{username}:{expires_at}"
    signed = f"{message}:{sign_session(message)}"
    return base64.urlsafe_b64encode(signed.encode("utf-8")).decode("ascii")


def valid_session(value: str | None) -> bool:
    if not auth_enabled():
        return True
    if not value:
        return False
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        username, expires_at, signature = decoded.rsplit(":", 2)
    except (ValueError, UnicodeDecodeError):
        return False
    message = f"{username}:{expires_at}"
    if not hmac.compare_digest(signature, sign_session(message)):
        return False
    if username != DASHBOARD_USERNAME:
        return False
    return int(expires_at) >= int(time.time())


def request_has_dashboard_access(request: Request) -> bool:
    return valid_session(request.cookies.get(SESSION_COOKIE))


def require_dashboard_access(request: Request) -> None:
    if not request_has_dashboard_access(request):
        raise HTTPException(status_code=401, detail="login required")


def request_has_config_access(request: Request) -> bool:
    return request_has_dashboard_access(request) or token_is_valid(request.headers.get("x-capture-token", ""))


def current_config() -> Dict[str, Any]:
    target_url = store.get_setting("target_url", configured_default_target())
    target = normalize_target_url(target_url)
    include_subdomains = store.get_setting("include_subdomains", True)
    return {
        **target,
        "include_subdomains": bool(include_subdomains),
        "dashboard": "Packet Capture Monitor",
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
    if not hmac.compare_digest(username, DASHBOARD_USERNAME) or not hmac.compare_digest(
        password, DASHBOARD_PASSWORD
    ):
        raise HTTPException(status_code=401, detail="invalid username or password")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        SESSION_COOKIE,
        make_session_value(username),
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
async def config(request: Request) -> Dict[str, Any]:
    if not request_has_config_access(request):
        raise HTTPException(status_code=401, detail="login required")
    return current_config()


@app.put("/api/config")
async def update_config(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    require_dashboard_access(request)
    try:
        target = normalize_target_url(str(payload.get("target_url") or payload.get("target_host") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store.set_setting("target_url", target["target_url"])
    if "include_subdomains" in payload:
        store.set_setting("include_subdomains", bool(payload["include_subdomains"]))

    updated = current_config()
    await manager.broadcast({"type": "config", "config": updated})
    return updated


@app.get("/api/captures")
async def list_captures(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    q: str = "",
    method: str = "",
    status: str = "",
) -> Dict[str, Any]:
    require_dashboard_access(request)
    return {
        "items": store.list(limit=limit, query=q.strip(), method=method.strip(), status=status.strip()),
        "stats": store.stats(),
    }


@app.get("/api/captures/{capture_id}")
async def get_capture(request: Request, capture_id: str) -> Dict[str, Any]:
    require_dashboard_access(request)
    capture = store.get(capture_id)
    if not capture:
        raise HTTPException(status_code=404, detail="capture not found")
    return capture


@app.post("/api/captures/events")
async def capture_event(
    payload: Dict[str, Any] = Body(...),
    x_capture_token: str = Header(default=""),
) -> Dict[str, Any]:
    verify_token(x_capture_token)
    try:
        record = store.upsert_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = store.summarize(record)
    await manager.broadcast({"type": "capture", "item": summary, "stats": store.stats()})
    return {"ok": True, "item": summary}


@app.delete("/api/captures")
async def clear_captures(request: Request) -> Dict[str, Any]:
    require_dashboard_access(request)
    store.clear()
    await manager.broadcast({"type": "clear", "stats": store.stats()})
    return {"ok": True}


@app.websocket("/ws/captures")
async def captures_ws(websocket: WebSocket) -> None:
    if auth_enabled() and not valid_session(websocket.cookies.get(SESSION_COOKIE)):
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        await websocket.send_json(
            {"type": "snapshot", "items": store.list(limit=200), "stats": store.stats()}
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
