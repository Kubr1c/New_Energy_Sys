"""Authentication module for the visualization API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from . import database
from .db_models import User


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_APP_ENV = os.environ.get("NES_APP_ENV", os.environ.get("APP_ENV", "development")).lower()
_DEFAULT_DEMO_SECRET = "new-energy-sys-demo-secret-2026"
_SECRET = os.environ.get("NES_JWT_SECRET", _DEFAULT_DEMO_SECRET)
_TOKEN_EXPIRE_SECONDS = 24 * 3600

if _APP_ENV in {"production", "prod"} and _SECRET == _DEFAULT_DEMO_SECRET:
    raise RuntimeError("NES_JWT_SECRET must be set to a non-default value in production.")

_DEMO_USERS: dict[str, dict] = {
    "admin": {
        "password_hash": hashlib.sha256("admin123".encode()).hexdigest(),
        "role": "admin",
        "display_name": "System Admin",
    },
    "guest": {
        "password_hash": hashlib.sha256("guest123".encode()).hexdigest(),
        "role": "viewer",
        "display_name": "Guest User",
    },
}


def _load_file_users() -> dict[str, dict]:
    """Load file/env users for legacy file-backed development mode."""
    raw_users = os.environ.get("NES_USERS_JSON")
    if raw_users:
        users = json.loads(raw_users)
        if not isinstance(users, dict) or not users:
            raise RuntimeError("NES_USERS_JSON must be a non-empty JSON object.")
        for username, record in users.items():
            if not isinstance(record, dict):
                raise RuntimeError(f"User record for {username!r} must be an object.")
            if "password_hash" not in record or "role" not in record:
                raise RuntimeError(f"User record for {username!r} must include password_hash and role.")
            record.setdefault("display_name", username)
        return users

    if _APP_ENV in {"production", "prod"} and not database.is_database_enabled():
        raise RuntimeError("NES_USERS_JSON or NES_DATABASE_URL must be configured in production.")

    return _DEMO_USERS


_FILE_USERS: dict[str, dict] = _load_file_users()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UserInfo:
    username: str
    role: str
    display_name: str


def _get_user_record(username: str) -> dict | None:
    """Read the active user from MySQL when enabled, otherwise from file mode."""
    if not database.is_database_enabled():
        return _FILE_USERS.get(username)
    with database.session_scope() as session:
        row = session.get(User, username)
        if row is None or not row.active:
            return None
        return {
            "password_hash": row.password_hash,
            "role": row.role,
            "display_name": row.display_name,
        }


# ---------------------------------------------------------------------------
# JWT helpers (minimal, no external deps)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(payload: str) -> str:
    return _b64url_encode(hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).digest())


def create_token(username: str, role: str) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "sub": username,
        "role": role,
        "exp": int(time.time()) + _TOKEN_EXPIRE_SECONDS,
    }).encode())
    signature = _sign(f"{header}.{payload}")
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> Optional[UserInfo]:
    """Return UserInfo if valid, else None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, payload, signature = parts
        expected_sig = _sign(f"{header}.{payload}")
        if not hmac.compare_digest(signature, expected_sig):
            return None
        data = json.loads(_b64url_decode(payload))
        if data.get("exp", 0) < time.time():
            return None
        username = data.get("sub", "")
    except Exception:
        return None
    user_record = _get_user_record(username)
    if not user_record:
        return None
    return UserInfo(
        username=username,
        role=str(user_record.get("role") or data.get("role") or "viewer"),
        display_name=str(user_record.get("display_name") or username),
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def authenticate(username: str, password: str) -> Optional[str]:
    """Return JWT token string if credentials are valid, else None."""
    user = _get_user_record(username)
    if not user:
        return None
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    if not hmac.compare_digest(pw_hash, str(user["password_hash"])):
        return None
    return create_token(username, str(user["role"]))
