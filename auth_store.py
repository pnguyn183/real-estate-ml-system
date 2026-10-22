"""SQLite-backed credentials for locally registered HomeNest accounts."""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import storage


PBKDF2_ITERATIONS = 600_000
_DUMMY_SALT = b"homenest-unknown-account"
_DUMMY_HASH = hashlib.pbkdf2_hmac(
    "sha256", b"not-a-real-password", _DUMMY_SALT, PBKDF2_ITERATIONS,
)
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BROWSER_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_MAX_BROWSER_SESSIONS = 20


def browser_session_hash(handle):
    """Validate the opaque bearer credential before hashing or querying it."""
    if not isinstance(handle, str) or not _BROWSER_SESSION_PATTERN.fullmatch(handle):
        raise ValueError("Invalid browser session")
    return hashlib.sha256(handle.encode("ascii")).hexdigest()


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def validate_password(password):
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Mật khẩu cần có từ 12 đến 128 ký tự.")
    if password.strip() != password:
        raise ValueError("Mật khẩu không được bắt đầu hoặc kết thúc bằng dấu cách.")


def validate_contact_email(email):
    normalized = email.strip()
    if normalized and (len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized)):
        raise ValueError("Vui lòng nhập địa chỉ email liên hệ hợp lệ.")
    return normalized


def database_path():
    configured_dir = os.environ.get("HOMENEST_DATA_DIR")
    data_dir = Path(configured_dir) if configured_dir else storage.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "accounts.sqlite3"


def validate_registration(email, full_name, password):
    normalized_email = email.strip().casefold()
    name = full_name.strip()
    if len(normalized_email) > 254 or not _EMAIL_PATTERN.fullmatch(normalized_email):
        raise ValueError("Vui lòng nhập địa chỉ email hợp lệ.")
    if not name or len(name) > 120:
        raise ValueError("Họ và tên cần có từ 1 đến 120 ký tự.")
    validate_password(password)
    return normalized_email, name


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


class AccountStore:
    def __init__(self, path=None):
        self.path = Path(path or database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    email TEXT PRIMARY KEY COLLATE NOCASE,
                    full_name TEXT NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL DEFAULT 'User'
                        CHECK (role IN ('User', 'Manager', 'Admin')),
                    is_active INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Additive migration: existing credentials and roles remain intact.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}
            additions = {
                "updated_at": "TEXT",
                "last_login_at": "TEXT",
                "admin_note": "TEXT NOT NULL DEFAULT ''",
                "disabled_reason": "TEXT NOT NULL DEFAULT ''",
                "session_version": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in additions.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE accounts ADD COLUMN {column} {definition}")
            connection.execute("UPDATE accounts SET updated_at = created_at WHERE updated_at IS NULL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS account_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_email TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    details TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS account_audit_target "
                               "ON account_audit (target_email, id)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    handle_hash TEXT PRIMARY KEY,
                    email TEXT NOT NULL COLLATE NOCASE,
                    session_version INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS browser_sessions_email "
                               "ON browser_sessions (email, created_at)")

    def create_browser_session(self, email, session_version, expires_at):
        """Persist only a digest, bounded by the authenticated token's expiry.

        The write lock covers the version check and insertion so an account
        reset or lock cannot produce a handle for the new credential version.
        """
        now = int(time.time())
        if (type(session_version) is not int or type(expires_at) is not int
                or expires_at <= now):
            raise ValueError("Invalid browser session")
        normalized_email = email.strip().casefold()
        handle = secrets.token_urlsafe(32)
        handle_hash = browser_session_hash(handle)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT role, is_active, session_version FROM accounts WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if (account is None or not account["is_active"]
                    or account["role"] not in {"User", "Manager", "Admin"}
                    or account["session_version"] != session_version):
                raise ValueError("Invalid browser session")
            connection.execute(
                "DELETE FROM browser_sessions WHERE email = ? "
                "AND (expires_at <= ? OR session_version != ?)",
                (normalized_email, now, session_version),
            )
            # Retain the newest handles; refreshing a page never creates one.
            connection.execute(
                "DELETE FROM browser_sessions WHERE email = ? AND handle_hash NOT IN "
                "(SELECT handle_hash FROM browser_sessions WHERE email = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                (normalized_email, normalized_email, _MAX_BROWSER_SESSIONS - 1),
            )
            connection.execute(
                "INSERT INTO browser_sessions "
                "(handle_hash, email, session_version, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (handle_hash, normalized_email, session_version, expires_at, now),
            )
        return {"browser_session": handle, "expires_at": expires_at}

    def restore_browser_session(self, handle):
        """Read identity and credential version together; never extend expiry."""
        handle_hash = browser_session_hash(handle)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT accounts.*, browser_sessions.expires_at AS browser_expires_at "
                "FROM browser_sessions JOIN accounts ON accounts.email = browser_sessions.email "
                "WHERE browser_sessions.handle_hash = ? AND browser_sessions.expires_at > ? "
                "AND accounts.is_active = 1 "
                "AND accounts.role IN ('User', 'Manager', 'Admin') "
                "AND accounts.session_version = browser_sessions.session_version",
                (handle_hash, int(time.time())),
            ).fetchone()
        if row is None:
            raise ValueError("Invalid browser session")
        return self._public_user(row), row["session_version"], row["browser_expires_at"]

    def revoke_browser_session(self, handle):
        handle_hash = browser_session_hash(handle)
        with self._connection() as connection:
            connection.execute("DELETE FROM browser_sessions WHERE handle_hash = ?", (handle_hash,))

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_user(self, email, full_name, password):
        return self.create_account(email, full_name, password, "User")

    def create_account(self, email, full_name, password, role, *, actor=None):
        normalized_email, name = validate_registration(email, full_name, password)
        if role not in {"User", "Manager", "Admin"}:
            raise ValueError("Vai trò tài khoản không hợp lệ.")
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        try:
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO accounts "
                    "(email, full_name, password_salt, password_hash, role, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (normalized_email, name, salt, password_hash, role,
                     utc_timestamp(), utc_timestamp()),
                )
                if actor:
                    self._audit(connection, normalized_email, actor, "create", {"full_name": name})
        except sqlite3.IntegrityError as error:
            raise ValueError("Địa chỉ email này đã được đăng ký.") from error
        return self.get_user(normalized_email)

    def authenticate(self, email, password):
        authenticated = self.authenticate_session(email, password)
        return authenticated[0] if authenticated else None

    def authenticate_session(self, email, password):
        """Return the verified credential version with its public identity.

        A password reset racing with login must not issue a fresh token for the
        password that has just been replaced.
        """
        normalized_email = email.strip().casefold()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE email = ?", (normalized_email,),
            ).fetchone()
        if row is None:
            hmac.compare_digest(_hash_password(password, _DUMMY_SALT), _DUMMY_HASH)
            return None
        if not row["is_active"]:
            hmac.compare_digest(_hash_password(password, row["password_salt"]), row["password_hash"])
            return None
        candidate = _hash_password(password, row["password_salt"])
        if not hmac.compare_digest(candidate, row["password_hash"]):
            return None
        last_login_at = utc_timestamp()
        with self._connection() as connection:
            updated = connection.execute(
                "UPDATE accounts SET last_login_at = ? WHERE email = ? AND is_active = 1 "
                "AND session_version = ? AND password_hash = ?",
                (last_login_at, normalized_email, row["session_version"], row["password_hash"]),
            )
            if updated.rowcount != 1:
                return None
        return {**self._public_user(row), "last_login_at": last_login_at}, row["session_version"]

    def get_user(self, email):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE email = ?", (email.strip().casefold(),),
            ).fetchone()
        return self._public_user(row) if row else None

    def get_session_version(self, email):
        with self._connection() as connection:
            row = connection.execute("SELECT session_version FROM accounts WHERE email = ?",
                                     (email.strip().casefold(),)).fetchone()
        return row["session_version"] if row else None

    def list_users(self):
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM accounts WHERE role = 'User' "
                                      "ORDER BY created_at DESC, email").fetchall()
        return [self._public_user(row) for row in rows]

    @staticmethod
    def _require_managed_user(connection, email):
        row = connection.execute("SELECT * FROM accounts WHERE email = ?",
                                 (email.strip().casefold(),)).fetchone()
        if row is None:
            raise LookupError("Không tìm thấy tài khoản User này.")
        if row["role"] != "User":
            raise PermissionError("Chỉ được quản lý tài khoản có vai trò User.")
        return row

    @staticmethod
    def _audit(connection, email, actor, action, details):
        connection.execute(
            "INSERT INTO account_audit (target_email, actor, action, created_at, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, actor, action, utc_timestamp(), json.dumps(details, ensure_ascii=False)),
        )

    def audit_history(self, email, limit=100):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, actor, action, created_at, details FROM account_audit "
                "WHERE target_email = ? ORDER BY id DESC LIMIT ?",
                (email.strip().casefold(), min(max(int(limit), 1), 100)),
            ).fetchall()
        records = [{**dict(row), "details": json.loads(row["details"])} for row in rows]
        return [{**record, "reason": record["details"].get("reason", "")} for record in records]

    def update_user(self, email, full_name, admin_note, *, actor, changed_fields=(), persist_profile=None):
        name = full_name.strip()
        if not name or len(name) > 120:
            raise ValueError("Họ và tên cần có từ 1 đến 120 ký tự.")
        if len(admin_note) > 2000:
            raise ValueError("Ghi chú quản trị không được vượt quá 2000 ký tự.")
        with self._connection() as connection:
            row = self._require_managed_user(connection, email)
            connection.execute("UPDATE accounts SET full_name = ?, admin_note = ?, updated_at = ? "
                               "WHERE email = ?",
                               (name, admin_note.strip(), utc_timestamp(), row["email"]))
            self._audit(connection, row["email"], actor, "update",
                        {"changed_fields": sorted(set(changed_fields) | {"full_name", "admin_note"})})
            if persist_profile:
                persist_profile()
        return self.get_user(email)

    def update_profile(self, email, full_name, *, persist_profile=None):
        name = full_name.strip()
        if not name or len(name) > 120:
            raise ValueError("Họ và tên cần có từ 1 đến 120 ký tự.")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE email = ?",
                                     (email.strip().casefold(),)).fetchone()
            if row is None:
                raise LookupError("Không tìm thấy tài khoản này.")
            if not row["is_active"] or row["role"] not in {"User", "Manager"}:
                raise PermissionError("Tài khoản không có quyền cập nhật hồ sơ cá nhân.")
            connection.execute("UPDATE accounts SET full_name = ?, updated_at = ? WHERE email = ?",
                               (name, utc_timestamp(), row["email"]))
            self._audit(connection, row["email"], row["email"], "profile_update",
                        {"changed_fields": ["full_name", "phone", "bio", "contact_email"]})
            if persist_profile:
                persist_profile()
        return self.get_user(email)

    def set_user_status(self, email, is_active, reason, *, actor):
        reason = reason.strip()
        if type(is_active) is not bool:
            raise ValueError("Trạng thái tài khoản không hợp lệ.")
        if not is_active and not reason:
            raise ValueError("Vui lòng nhập lý do khóa tài khoản.")
        if len(reason) > 1000:
            raise ValueError("Lý do không được vượt quá 1000 ký tự.")
        with self._connection() as connection:
            row = self._require_managed_user(connection, email)
            connection.execute(
                "UPDATE accounts SET is_active = ?, disabled_reason = ?, updated_at = ?, "
                "session_version = session_version + 1 WHERE email = ?",
                (int(is_active), "" if is_active else reason, utc_timestamp(), row["email"]),
            )
            self._audit(connection, row["email"], actor, "activate" if is_active else "disable",
                        {"is_active": is_active, "reason": reason})
        return self.get_user(email)

    def reset_user_password(self, email, password, reason, *, actor):
        validate_password(password)
        reason = reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("Vui lòng nhập lý do đặt lại mật khẩu (tối đa 1000 ký tự).")
        salt = secrets.token_bytes(16)
        hashed = _hash_password(password, salt)
        with self._connection() as connection:
            row = self._require_managed_user(connection, email)
            connection.execute(
                "UPDATE accounts SET password_salt = ?, password_hash = ?, updated_at = ?, "
                "session_version = session_version + 1 WHERE email = ?",
                (salt, hashed, utc_timestamp(), row["email"]),
            )
            self._audit(connection, row["email"], actor, "reset_password", {"reason": reason})
        return self.get_user(email)

    @staticmethod
    def _public_user(row):
        return {
            "email": row["email"],
            "full_name": row["full_name"],
            "role": row["role"],
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
            "admin_note": row["admin_note"],
            "disabled_reason": row["disabled_reason"],
        }
