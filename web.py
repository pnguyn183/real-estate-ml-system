"""HomeNest HTTP entry point, including same-origin HttpOnly login cookies."""

import json
import math
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import streamlit as st
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from api_client import ValuationAPIError, restore_browser_session, revoke_browser_session


COOKIE_NAME = "homenest_session"


class BrowserSessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            scope = {**scope, "headers": [
                (key, value) for key, value in scope.get("headers", [])
                if key.lower() != b"x-homenest-session"
            ] + [(b"x-homenest-session", b"1")]}
        await self.app(scope, receive, send)


def response(body, status=200):
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def same_origin(request):
    origin = urlsplit(request.headers.get("origin", ""))
    return (origin.scheme == request.url.scheme and origin.netloc == request.url.netloc
            and not origin.path and not origin.query and not origin.fragment)


def create_session_routes(base_path=None):
    base_path = st.get_option("server.baseUrlPath") if base_path is None else base_path
    prefix = "/" + base_path.strip("/") if base_path.strip("/") else ""
    cookie_path = prefix + "/"

    async def session_cookie(request):
        if not same_origin(request):
            return response({"error": "Yêu cầu không hợp lệ."}, 403)
        secure = request.url.scheme == "https"
        if request.method == "DELETE":
            handle = request.cookies.get(COOKIE_NAME)
            if handle:
                try:
                    await run_in_threadpool(revoke_browser_session, handle)
                except ValuationAPIError:
                    pass
            result = response({"revoked": True})
            result.delete_cookie(COOKIE_NAME, path=cookie_path, secure=secure,
                                 httponly=True, samesite="lax")
            return result
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            return response({"error": "Nội dung yêu cầu không hợp lệ."}, 415)
        body = await request.body()
        if len(body) > 1024:
            return response({"error": "Nội dung yêu cầu không hợp lệ."}, 413)
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"browser_session"}:
                raise ValueError("Invalid payload")
            handle = payload["browser_session"]
            if not isinstance(handle, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", handle):
                raise ValueError("Invalid handle")
        except (ValueError, TypeError):
            return response({"error": "Thông tin phiên không hợp lệ."}, 400)
        try:
            account = await run_in_threadpool(restore_browser_session, handle)
            expires_at = float(account["expires_at"])
            if not math.isfinite(expires_at):
                raise ValueError("Invalid session expiry")
            remaining = int(expires_at - time.time())
            if remaining <= 0:
                raise ValuationAPIError("Expired session")
        except (ValuationAPIError, KeyError, OverflowError, TypeError, ValueError):
            return response({"error": "Phiên đăng nhập đã hết hạn hoặc không còn hiệu lực."}, 401)
        result = response({"persisted": True})
        result.set_cookie(COOKIE_NAME, handle, max_age=remaining, path=cookie_path,
                          secure=secure, httponly=True, samesite="lax")
        return result

    return [Route(prefix + "/homenest/auth/session", session_cookie, methods=["POST", "DELETE"])]


app = st.App(str(Path(__file__).with_name("app.py")), routes=create_session_routes(),
             middleware=[Middleware(BrowserSessionMiddleware)])
