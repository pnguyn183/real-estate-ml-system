"""Keep browser login through refresh with an opaque, HttpOnly server session."""

import re
from uuid import uuid4

import streamlit as st

from api_client import (ValuationAPIError, create_browser_session,
                        restore_browser_session, revoke_browser_session)


COOKIE_NAME = "homenest_session"
BRIDGE_KEY = "homenest_cookie_bridge"
_HANDLE = re.compile(r"[A-Za-z0-9_-]{43}\Z")

_COOKIE_BRIDGE = st.components.v2.component(
    "homenest_cookie_session",
    js="""
export default function(component) {
    const { data, setStateValue } = component;
    const controller = new AbortController();
    const options = {
        method: data.operation === 'save' ? 'POST' : 'DELETE',
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
    };
    if (data.operation === 'save') {
        options.headers = {'Content-Type': 'application/json'};
        options.body = JSON.stringify({browser_session: data.handle});
    }
    fetch(data.endpoint, options).then(async response => {
        const body = await response.json();
        const ok = response.ok && (data.operation === 'save' ? body.persisted === true : body.revoked === true);
        setStateValue('result', {id: data.id, ok});
    }).catch(error => {
        if (error.name !== 'AbortError') setStateValue('result', {id: data.id, ok: false});
    });
    return () => controller.abort();
}
""",
)


def enabled():
    """The ASGI entry point advertises its same-origin cookie endpoints."""
    return st.context.headers.get("x-homenest-session") == "1"


def browser_handle():
    candidate = st.session_state.get("_browser_session_handle") or st.context.cookies.get(COOKIE_NAME)
    return candidate if isinstance(candidate, str) and _HANDLE.fullmatch(candidate) else None


def queue_cookie_clear():
    st.session_state["_browser_restore_checked"] = True
    st.session_state["_browser_cookie_operation"] = {"operation": "clear", "id": uuid4().hex}
    st.session_state.pop("_browser_session_handle", None)


def revoke_current_session():
    handle = browser_handle()
    if handle:
        try:
            revoke_browser_session(handle)
        except ValuationAPIError:
            # Cookie deletion at the gateway remains possible when the API is down.
            pass


def remember_login(account):
    if not enabled():
        return
    st.session_state["_browser_restore_checked"] = True
    try:
        persisted = create_browser_session(account["access_token"])
    except ValuationAPIError:
        st.session_state["_browser_cookie_error"] = (
            "Đã đăng nhập nhưng chưa lưu được phiên trên trình duyệt. Vui lòng thử đăng nhập lại nếu tải lại trang.")
        return
    handle = persisted["browser_session"]
    st.session_state["_browser_session_handle"] = handle
    st.session_state["_browser_cookie_operation"] = {
        "operation": "save", "handle": handle, "id": uuid4().hex,
    }


def restore_login():
    if not enabled() or st.session_state.get("auth_mode") or st.session_state.get("_browser_restore_checked"):
        return
    st.session_state["_browser_restore_checked"] = True
    handle = browser_handle()
    if not handle:
        return
    try:
        account = restore_browser_session(handle)
    except ValuationAPIError:
        queue_cookie_clear()
        st.session_state.update(
            public_page="auth", auth_screen="login",
            auth_notice="Phiên đăng nhập đã hết hạn hoặc không còn hiệu lực. Vui lòng đăng nhập lại.",
        )
        return
    st.session_state.update(
        logged_in=True, auth_mode="internal", auth_token=account["access_token"],
        auth_expires_at=account["expires_at"], username=account["email"],
        display_name=account["full_name"], role=account["role"],
        _browser_session_handle=handle,
    )


def _cookie_result():
    value = st.session_state.get(BRIDGE_KEY, {})
    result = value.get("result") if hasattr(value, "get") else getattr(value, "result", None)
    operation = st.session_state.get("_browser_cookie_operation", {})
    if not isinstance(result, dict) or result.get("id") != operation.get("id"):
        return
    if result.get("ok"):
        st.session_state.pop("_browser_cookie_operation", None)
        st.session_state.pop("_browser_cookie_error", None)
    else:
        st.session_state["_browser_cookie_error"] = "Chưa lưu được trạng thái đăng nhập trên trình duyệt. Vui lòng thử lại."
        st.session_state.pop("_browser_cookie_operation", None)


def sync_cookie():
    if not enabled():
        return
    operation = st.session_state.get("_browser_cookie_operation")
    if operation:
        prefix = st.get_option("server.baseUrlPath").strip("/")
        endpoint = ("/" + prefix if prefix else "") + "/homenest/auth/session"
        _COOKIE_BRIDGE(key=BRIDGE_KEY, data={**operation, "endpoint": endpoint},
                       height=0, on_result_change=_cookie_result)
    if message := st.session_state.get("_browser_cookie_error"):
        st.warning(message)
