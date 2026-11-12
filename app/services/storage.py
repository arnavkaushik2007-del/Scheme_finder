"""Encrypted file store. Files are Fernet-encrypted (AES-128-CBC + HMAC) before they touch disk;
the key comes from FERNET_KEY and is never stored in the database."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class StorageError(Exception):
    pass


def _dir() -> Path:
    d = Path(get_settings().file_storage_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key.encode())


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(data: bytes) -> str:
    key = f"{uuid.uuid4().hex}.enc"
    (_dir() / key).write_bytes(_fernet().encrypt(data))
    return key


def get(key: str) -> bytes:
    if not key.endswith(".enc") or "/" in key or "\\" in key or ".." in key:
        raise StorageError("bad_key")
    path = _dir() / key
    if not path.is_file():
        raise StorageError("missing")
    try:
        return _fernet().decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise StorageError("undecryptable") from exc


def delete(key: str | None) -> None:
    if not key or not key.endswith(".enc") or "/" in key or "\\" in key or ".." in key:
        return
    (_dir() / key).unlink(missing_ok=True)
