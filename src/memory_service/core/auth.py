"""Password hashing, JWT session tokens, API key generation.

Design notes:

- **Passwords**: bcrypt via `passlib`. Cost left at default (12) — fast enough
  on commodity hardware, slow enough to make brute force impractical.
- **Session tokens**: short-lived JWT (HS256), signed with the app SECRET_KEY.
  Lifetime in `Settings.session_lifetime_minutes`. Subject is the user id.
- **API keys**: high-entropy random tokens, returned plaintext only at
  creation. Storage is `sha256(plaintext)` because the entropy is already
  there — bcrypt would force per-row scans on every authenticated request.
  Format: `mk_<32 hex chars>`. The `mk_` prefix is for grep + dashboards.

Naming: "mk" = "memory key". Not a hard contract; just human-friendly.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from memory_service.config import Settings

API_KEY_PREFIX = "mk_"

# bcrypt has a hard 72-byte limit on inputs. Pre-hashing with SHA-256 lets
# users pass arbitrarily long passwords without surprises. The SHA-256
# digest goes through bcrypt for the costly stretch step.
def _prepare(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- session tokens (JWT) ----------


def issue_session_token(user_id: str, settings: Settings) -> tuple[str, datetime]:
    """Return (token, expires_at). Expiry returned so callers can show it."""
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.session_lifetime_minutes)
    payload = {"sub": user_id, "exp": expires_at, "iat": datetime.now(UTC), "type": "session"}
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_session_token(token: str, settings: Settings) -> str | None:
    """Return user_id if the token is valid + unexpired, else None.

    Intentionally swallows JWTError — caller decides what 401 message to send.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "session":
            return None
        sub = payload.get("sub")
        return sub if isinstance(sub, str) else None
    except JWTError:
        return None


# ---------- API keys ----------


def generate_api_key() -> str:
    """Return a fresh plaintext API key. Caller is responsible for showing
    it to the user exactly once (we never persist this value).
    """
    return f"{API_KEY_PREFIX}{secrets.token_hex(32)}"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def api_key_prefix(key: str) -> str:
    """The first 8 chars of the body (after the `mk_`), for human display."""
    body = key.removeprefix(API_KEY_PREFIX)
    return f"{API_KEY_PREFIX}{body[:8]}"
