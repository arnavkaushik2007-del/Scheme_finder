"""Time-based one-time passwords (RFC 6238, SHA-1, 6 digits, 30 s) for optional officer/admin 2FA.

Works with any authenticator app (Google Authenticator, Microsoft Authenticator, Aegis, ...).
The shared secret is stored Fernet-encrypted, never in plain text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet

from app.config import get_settings


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def now_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // 30))


def verify(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return False
    counter = int((at or time.time()) // 30)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in range(-window, window + 1))


def uri(secret: str, account: str, issuer: str = "Scheme Finder") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


def encrypt(secret: str) -> str:
    return Fernet(get_settings().fernet_key.encode()).encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    return Fernet(get_settings().fernet_key.encode()).decrypt(token.encode()).decode()
