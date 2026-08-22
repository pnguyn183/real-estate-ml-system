"""Authentication and role-based access control helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from fastapi import HTTPException, status

Role = Literal["admin", "manager", "user"]

ROLES: tuple[Role, ...] = ("admin", "manager", "user")

DEFAULT_TOKEN_EXPIRE_MINUTES = 60
PBKDF2_ITERATIONS = 260_000
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    full_name: str
    role: Role
    password_hash: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str] = None

    def public(self) -> Dict[str, Any]:
        # Return a serializable public view of the user record
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_login_at": self.last_login_at,
        }


class FileUserStore:
    """Small JSON-backed user store suitable for local deployment and demos."""

    def __init__(self, path: Path):
        # Initialize JSON-backed user store with a reentrant lock
        self.path = path
        self._lock = threading.RLock()

    def all_users(self) -> list[UserRecord]:
        # Return all users from the store as UserRecord instances
        with self._lock:
            data = self._read()
            return [self._to_user(item) for item in data.get("users", [])]

    def get_by_email(self, email: str) -> Optional[UserRecord]:
        # Lookup a user by normalized email
        normalized = normalize_email(email)
        for user in self.all_users():
            if user.email == normalized:
                return user
        return None

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        # Lookup a user by id
        for user in self.all_users():
            if user.id == user_id:
                return user
        return None

    def save(self, user: UserRecord) -> UserRecord:
        # Save or update a user record atomically to the JSON store
        with self._lock:
            data = self._read()
            users = data.get("users", [])
            for index, existing in enumerate(users):
                if existing["id"] == user.id:
                    users[index] = user.__dict__
                    break
            else:
                users.append(user.__dict__)
            data["users"] = users
            self._write(data)
        return user

    def count(self) -> int:
        # Return total number of users
        return len(self.all_users())

    def count_active_admins(self) -> int:
        # Count active admin users
        return sum(1 for user in self.all_users() if user.role == "admin" and user.is_active)

    def _read(self) -> Dict[str, Any]:
        # Read raw JSON from the store (not thread-safe; caller must lock)
        if not self.path.exists():
            return {"users": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: Dict[str, Any]) -> None:
        # Atomically write JSON to disk via temporary file replace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _to_user(data: Dict[str, Any]) -> UserRecord:
        # Convert raw dict to a UserRecord instance
        return UserRecord(
            id=data["id"],
            email=data["email"],
            full_name=data.get("full_name") or data["email"],
            role=data["role"],
            password_hash=data["password_hash"],
            is_active=bool(data.get("is_active", True)),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            last_login_at=data.get("last_login_at"),
        )


class AuthService:
    def __init__(
        self,
        store: FileUserStore,
        secret_key: str,
        token_expire_minutes: int = DEFAULT_TOKEN_EXPIRE_MINUTES,
    ):
        # Initialize AuthService with secure secret and token expiry
        if not secret_key or len(secret_key) < 32:
            raise ValueError("AUTH_SECRET_KEY must be at least 32 characters")
        self.store = store
        self.secret_key = secret_key.encode("utf-8")
        self.token_expire_seconds = token_expire_minutes * 60

    def register(self, email: str, password: str, full_name: str = "") -> UserRecord:
        # Register a new user and return the created UserRecord
        email = normalize_email(email)
        validate_email(email)
        validate_password(password)

        if self.store.get_by_email(email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        role: Role = "admin" if self.store.count() == 0 else "user"
        now = utc_timestamp()
        user = UserRecord(
            id=secrets.token_urlsafe(16),
            email=email,
            full_name=full_name.strip() or email,
            role=role,
            password_hash=hash_password(password),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return self.store.save(user)

    def authenticate(self, email: str, password: str) -> UserRecord:
        # Authenticate credentials and update last_login_at
        user = self.store.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is disabled")

        updated = user.__dict__.copy()
        now = utc_timestamp()
        updated["last_login_at"] = now
        updated["updated_at"] = now
        return self.store.save(UserRecord(**updated))

    def create_access_token(self, user: UserRecord) -> str:
        # Create a signed JWT-like access token (HMAC-SHA256)
        now = int(time.time())
        payload = {
            "sub": user.id,
            "email": user.email,
            "role": user.role,
            "iat": now,
            "exp": now + self.token_expire_seconds,
            "jti": secrets.token_urlsafe(12),
        }
        return encode_token(payload, self.secret_key)

    def user_from_token(self, token: str) -> UserRecord:
        # Validate token and return corresponding UserRecord
        payload = decode_token(token, self.secret_key)
        user_id = payload.get("sub")
        if not isinstance(user_id, str):
            raise_invalid_token()
        user = self.store.get_by_id(user_id)
        if not user:
            raise_invalid_token()
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is disabled")
        if payload.get("role") != user.role:
            raise_invalid_token()
        return user

    def list_users(self) -> list[UserRecord]:
        # Return sorted list of users by creation time
        return sorted(self.store.all_users(), key=lambda user: user.created_at)

    def update_user_role(self, user_id: str, role: Role, actor: UserRecord) -> UserRecord:
        # Update a user's role with admin-safety checks
        validate_role(role)
        user = self._existing_user(user_id)
        if user.id == actor.id and user.role == "admin" and role != "admin":
            if self.store.count_active_admins() <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove the last admin")
        if user.role == "admin" and role != "admin" and self.store.count_active_admins() <= 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove the last admin")

        updated = user.__dict__.copy()
        updated["role"] = role
        updated["updated_at"] = utc_timestamp()
        return self.store.save(UserRecord(**updated))

    def set_user_active(self, user_id: str, is_active: bool, actor: UserRecord) -> UserRecord:
        # Enable or disable a user account with safety checks
        user = self._existing_user(user_id)
        if user.id == actor.id and not is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot disable your own account")
        if user.role == "admin" and user.is_active and not is_active and self.store.count_active_admins() <= 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot disable the last admin")

        updated = user.__dict__.copy()
        updated["is_active"] = is_active
        updated["updated_at"] = utc_timestamp()
        return self.store.save(UserRecord(**updated))

    def _existing_user(self, user_id: str) -> UserRecord:
        # Helper to fetch existing user or raise 404
        user = self.store.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return user


def require_minimum_role(user: UserRecord, allowed_roles: Iterable[Role]) -> None:
    # Enforce that user's role is within allowed roles
    if user.role not in set(allowed_roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


def normalize_email(email: str) -> str:
    # Normalize email to lowercase trimmed form
    return email.strip().lower()


def validate_email(email: str) -> None:
    # Validate email format or raise 422
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid email address")


def validate_password(password: str) -> None:
    # Enforce password complexity or raise 422
    if len(password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Password must be at least 8 characters")
    if not re.search(r"[a-z]", password):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Password must include a lowercase letter")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Password must include an uppercase letter")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Password must include a number")


def validate_role(role: str) -> None:
    # Validate role is one of allowed roles or raise 422
    if role not in ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid role")


def hash_password(password: str) -> str:
    # Hash password using PBKDF2-HMAC-SHA256 with random salt
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    # Verify a plaintext password against stored pbkdf2 hash in constant time
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def encode_token(payload: Dict[str, Any], secret_key: bytes) -> str:
    # Encode payload into a signed compact token (JWT-like HMAC-SHA256)
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = "{}.{}".format(_b64_json(header), _b64_json(payload))
    signature = hmac.new(secret_key, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64_bytes(signature)}"


def decode_token(token: str, secret_key: bytes) -> Dict[str, Any]:
    # Decode and verify token signature and expiry, returning payload dict
    try:
        header_text, payload_text, signature_text = token.split(".", 2)
        signing_input = f"{header_text}.{payload_text}"
        expected_signature = hmac.new(secret_key, signing_input.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64_bytes(expected_signature), signature_text):
            raise_invalid_token()
        header = json.loads(_b64_decode(header_text))
        payload = json.loads(_b64_decode(payload_text))
        if header.get("alg") != "HS256":
            raise_invalid_token()
        if int(payload.get("exp", 0)) < int(time.time()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
        return payload
    except HTTPException:
        raise
    except Exception:
        raise_invalid_token()


def _b64_json(data: Dict[str, Any]) -> str:
    # Helper: base64-url encode JSON without padding
    return _b64_bytes(json.dumps(data, separators=(",", ":")).encode("utf-8"))


def _b64_bytes(data: bytes) -> str:
    # Helper: base64-url encode bytes and strip padding
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64_decode(data: str) -> str:
    # Helper: decode base64-url string with padding handling
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


def utc_timestamp() -> str:
    # Return current UTC timestamp in ISO-like format
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def raise_invalid_token() -> None:
    # Raise standardized invalid-token HTTPException
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")


def build_auth_service() -> AuthService:
    # Construct AuthService using environment configuration (dev defaults allowed)
    users_path = Path(os.environ.get("AUTH_USERS_PATH", "artifacts/auth/users.json"))
    secret = os.environ.get("AUTH_SECRET_KEY", "")
    if not secret:
        secret = "dev-only-change-this-auth-secret-key-32"
    expire_minutes = int(os.environ.get("AUTH_TOKEN_EXPIRE_MINUTES", str(DEFAULT_TOKEN_EXPIRE_MINUTES)))
    return AuthService(FileUserStore(users_path), secret, expire_minutes)
