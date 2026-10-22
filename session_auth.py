"""Streamlit OIDC session helpers and server-side API token access."""

import time
import math
from collections.abc import Mapping

import streamlit as st

from identity import resolve_role
from api_client import get_account_identity, ValuationAPIError


def oidc_auth_configured():
    try:
        auth = st.secrets.get("auth", {})
    except FileNotFoundError:
        return False
    if not isinstance(auth, Mapping):
        return False
    required = ("redirect_uri", "cookie_secret", "client_id", "client_secret",
                "server_metadata_url")
    return all(auth.get(key) for key in required)


def oidc_id_token_exposed():
    auth = st.secrets.get("auth", {})
    exposed = auth.get("expose_tokens", []) if isinstance(auth, Mapping) else []
    if isinstance(exposed, str):
        exposed = [exposed]
    return "id" in exposed


def current_identity():
    if st.session_state.get("auth_mode") == "internal":
        try:
            expires_at = float(st.session_state.get("auth_expires_at", 0))
        except (TypeError, ValueError):
            expires_at = 0
        token = st.session_state.get("auth_token")
        notice = "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."
        if math.isfinite(expires_at) and expires_at > time.time() and token:
            try:
                user = get_account_identity(token)["user"]
                if not isinstance(user, Mapping):
                    raise ValueError("Invalid identity response")
                if user.get("is_active") is not True or user.get("role") not in {"User", "Manager", "Admin"}:
                    raise ValueError("Inactive or invalid identity")
                email = str(user["email"]).strip()
                if not email:
                    raise ValueError("Missing account identity")
                return {
                    "subject": email,
                    "username": email,
                    "role": user["role"],
                    "display_name": user["full_name"],
                }
            except ValuationAPIError as error:
                notice = f"Không thể xác thực phiên đăng nhập: {error}"
            except (KeyError, TypeError, ValueError):
                notice = "Thông tin phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại."
        # A revoked account must lose cached data and form values as well as its token.
        st.session_state.clear()
        st.session_state.update(auth_notice=notice, public_page="auth", auth_screen="login")
        return None
    if not oidc_auth_configured() or not st.user.is_logged_in:
        return None
    claims = st.user.to_dict()
    try:
        expires_at = float(claims["exp"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Nhà cung cấp OIDC không trả về thời hạn token hợp lệ.") from error
    if expires_at <= time.time():
        st.logout()
        return None

    role_settings = st.secrets.get("roles", {})
    if not isinstance(role_settings, Mapping):
        role_settings = {}
    role = resolve_role(claims, **{
        key: role_settings.get(key, ())
        for key in ("admin_groups", "manager_groups", "admin_emails", "manager_emails")
    })
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise RuntimeError("Nhà cung cấp OIDC không trả về định danh tài khoản.")
    return {
        "subject": subject,
        "username": subject,
        "role": role,
        "display_name": str(claims.get("name") or claims.get("email") or subject),
    }


def api_bearer_token():
    if st.session_state.get("auth_mode") == "internal":
        token = st.session_state.get("auth_token")
        if not token:
            raise RuntimeError("Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.")
        return token
    if oidc_auth_configured() and st.user.is_logged_in:
        try:
            return st.user.tokens["id"]
        except KeyError as error:
            raise RuntimeError("Không có token OIDC để xác thực với API.") from error
    raise RuntimeError("Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.")
