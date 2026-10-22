"""OIDC token verification for the private valuation API."""

import os
import threading
import time

import requests
from fastapi import HTTPException
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet


_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 300
_ALLOWED_ALGORITHMS = {
    "RS256", "RS384", "RS512", "ES256", "ES384", "ES512",
}


def _get_json(url):
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached and now < cached[0]:
            return cached[1]
    try:
        response = requests.get(url, timeout=(3, 8))
        response.raise_for_status()
        value = response.json()
    except (requests.RequestException, ValueError) as error:
        raise HTTPException(503, "Không thể xác minh danh tính với nhà cung cấp OIDC.") from error
    if not isinstance(value, dict):
        raise HTTPException(503, "Nhà cung cấp OIDC trả về metadata không hợp lệ.")
    with _CACHE_LOCK:
        _CACHE[url] = (now + _CACHE_TTL_SECONDS, value)
    return value


def verify_id_token(token):
    issuer = os.environ.get("AUTH_OIDC_ISSUER", "").rstrip("/")
    audience = os.environ.get("AUTH_OIDC_CLIENT_ID", "")
    metadata_url = os.environ.get("AUTH_OIDC_METADATA_URL", "")
    if not issuer or not audience or not metadata_url:
        raise HTTPException(503, "API chưa được cấu hình xác thực OIDC.")
    if not issuer.startswith("https://") or not metadata_url.startswith("https://"):
        raise HTTPException(503, "Issuer và metadata OIDC phải dùng HTTPS.")

    metadata = _get_json(metadata_url)
    if metadata.get("issuer", "").rstrip("/") != issuer:
        raise HTTPException(503, "Issuer trong cấu hình không khớp metadata OIDC.")
    jwks_uri = metadata.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
        raise HTTPException(503, "Metadata OIDC thiếu địa chỉ JWKS HTTPS hợp lệ.")

    try:
        key_set = KeySet.import_key_set(_get_json(jwks_uri))
        verified = jwt.decode(token, key_set, algorithms=sorted(_ALLOWED_ALGORITHMS))
    except JoseError as error:
        raise HTTPException(401, "Bearer token không hợp lệ hoặc đã hết hạn.") from error
    claims = verified.claims
    if not isinstance(claims, dict):
        raise HTTPException(401, "Bearer token không chứa claims hợp lệ.")

    now = time.time()
    try:
        expires_at = float(claims["exp"])
        issued_at = float(claims["iat"])
        not_before = float(claims.get("nbf", 0))
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(401, "Bearer token thiếu thời hạn hợp lệ.") from error
    if expires_at <= now or issued_at > now + 60 or not_before > now + 60:
        raise HTTPException(401, "Bearer token không hợp lệ hoặc đã hết hạn.")

    claim_issuer = str(claims.get("iss", "")).rstrip("/")
    claim_audience = claims.get("aud")
    audiences = [claim_audience] if isinstance(claim_audience, str) else claim_audience
    if (claim_issuer != issuer or not isinstance(audiences, list)
            or audience not in audiences or not claims.get("sub")):
        raise HTTPException(401, "Bearer token không dành cho dịch vụ này.")
    return dict(claims)


def role_settings_from_environment():
    def values(name, defaults=()):
        configured = os.environ.get(name)
        return [value.strip() for value in configured.split(",") if value.strip()] if configured else list(defaults)

    return {
        "admin_groups": values("AUTH_ADMIN_GROUPS", ("homenest-admin",)),
        "manager_groups": values("AUTH_MANAGER_GROUPS", ("homenest-manager",)),
        "admin_emails": values("AUTH_ADMIN_EMAILS"),
        "manager_emails": values("AUTH_MANAGER_EMAILS"),
    }
