"""Password hashing (Argon2id) and the password-strength policy."""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with library defaults (memory-hard)

MIN_LENGTH = 10
_COMMON = {"password123", "qwerty12345", "1234567890", "iloveindia1", "admin12345"}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# A fixed hash used to keep login timing similar when the account does not exist.
DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def password_problems(password: str, login_id: str = "") -> list[str]:
    """Return a list of machine codes for each rule the password breaks (empty = strong enough)."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append("too_short")
    if len(password) > 128:
        problems.append("too_long")
    if not re.search(r"[a-z]", password):
        problems.append("no_lower")
    if not re.search(r"[A-Z]", password):
        problems.append("no_upper")
    if not re.search(r"\d", password):
        problems.append("no_digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("no_symbol")
    if password.lower() in _COMMON:
        problems.append("too_common")
    if login_id and login_id.split("@")[0].lower() in password.lower() and len(login_id.split("@")[0]) >= 4:
        problems.append("contains_login_id")
    return problems


PASSWORD_RULES_EN = (
    f"At least {MIN_LENGTH} characters with an uppercase letter, a lowercase letter, a digit and a symbol."
)
