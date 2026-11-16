"""
Yerel kimlik. LDAP yoktur: kullanıcı ilk açılışta adını yazar, bir USER_ID (UUID) üretilir ve
ikisi .env'e yazılır. Bu bir kimlik doğrulama değildir; ekip içi güvene dayalı bir araçtır.

    from scripts.user import current_user
    current_user().name
"""
from __future__ import annotations

import getpass
import socket
import uuid
from dataclasses import dataclass

from scripts import config
from scripts.config import ENV


@dataclass(frozen=True)
class User:
    id: str
    name: str


def needs_name() -> bool:
    return not ENV.USER_NAME.strip()


def ensure_identity() -> None:
    if not ENV.USER_ID:
        config.save({"USER_ID": str(uuid.uuid4())})


def current_user() -> User:
    return User(ENV.USER_ID, ENV.USER_NAME.strip() or os_user())


def os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:   # noqa: BLE001
        return "?"


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:   # noqa: BLE001
        return "?"
