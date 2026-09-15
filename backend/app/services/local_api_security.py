from __future__ import annotations

import json
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEVELOPMENT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


class LocalBrowserOriginMiddleware:
    """Reject browser writes from non-loopback origins.

    Native clients and local scripts normally omit ``Origin`` and remain
    supported. Browser requests always carry it for cross-origin writes.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        origin = _header(scope, b"origin")
        if method in UNSAFE_METHODS and origin and not is_loopback_origin(origin):
            payload = json.dumps(
                {
                    "error": {
                        "code": "UNTRUSTED_BROWSER_ORIGIN",
                        "message": "浏览器来源不是本机页面，已拒绝修改本地数据。",
                        "detail": {},
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(payload)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        await self.app(scope, receive, send)


def is_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost":
            return True
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            try:
                return value.decode("latin-1").strip()
            except UnicodeDecodeError:
                return None
    return None
