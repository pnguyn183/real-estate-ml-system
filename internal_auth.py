"""Signed bearer tokens for locally registered HomeNest accounts."""

import base64
import json
import os
import time

from fastapi import HTTPException
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from auth_store import AccountStore


_ISSUER = "homenest-internal"
_AUDIENCE = "homenest-api"
_TOKEN_LIFETIME_SECONDS = 8 * 60 * 60
_ALLOWED_ALGORITHM = "HS256"


def _signing_secret():
    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(503, "API chưa được cấu hình khóa ký phiên nội bộ an toàn.")
    return secret.encode("utf-8")


def ensure_configured():
    _signing_secret()


def _token_payload(token):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def is_internal_token(token):
    return (_token_payload(token) or {}).get("iss") == _ISSUER


def issue_token(user, *, session_version=0, expires_at=None):
    now = int(time.time())
    if expires_at is not None and (type(expires_at) is not int or expires_at <= now):
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.")
    expires_at = min(now + _TOKEN_LIFETIME_SECONDS, expires_at) if expires_at is not None else now + _TOKEN_LIFETIME_SECONDS
    signing_key = OctKey.import_key(_signing_secret())
    token = jwt.encode(
        {"alg": _ALLOWED_ALGORITHM},
        {
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "sub": user["email"],
            "iat": now,
            "exp": expires_at,
            "session_version": session_version,
        },
        signing_key,
    )
    return {"access_token": token, "token_type": "bearer", "expires_at": expires_at}


def verify_internal_token(token, store=None):
    try:
        signing_key = OctKey.import_key(_signing_secret())
        verified = jwt.decode(token, signing_key, algorithms=[_ALLOWED_ALGORITHM])
    except JoseError as error:
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.") from error
    claims = verified.claims
    if not isinstance(claims, dict):
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ.")
    now = int(time.time())
    try:
        expires_at = int(claims["exp"])
        issued_at = int(claims["iat"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ.") from error
    email = str(claims.get("sub", "")).strip().casefold()
    if (claims.get("iss") != _ISSUER or claims.get("aud") != _AUDIENCE
            or not email or expires_at <= now or issued_at > now + 60):
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.")
    user = (store or AccountStore()).get_user(email)
    if user is None or not user["is_active"]:
        raise HTTPException(401, "Tài khoản không tồn tại hoặc đã bị vô hiệu hóa.")
    version = claims.get("session_version", 0)
    if type(version) is not int or version != (store or AccountStore()).get_session_version(email):
        raise HTTPException(401, "Phiên đăng nhập đã bị thu hồi. Vui lòng đăng nhập lại.")
    return {
        **claims,
        "email": user["email"],
        "name": user["full_name"],
        "app_role": user["role"],
    }
