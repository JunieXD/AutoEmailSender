from __future__ import annotations

import os
from typing import Literal, cast


BackendRole = Literal["api", "worker", "combined"]
BACKEND_ROLE_ENV = "AUTO_EMAIL_SENDER_BACKEND_ROLE"
BACKEND_ROLES: tuple[BackendRole, ...] = ("api", "worker", "combined")


def get_backend_role() -> BackendRole:
    raw_role = os.getenv(BACKEND_ROLE_ENV, "combined").strip().lower()
    if raw_role not in BACKEND_ROLES:
        raise RuntimeError(
            f"Invalid {BACKEND_ROLE_ENV}: {raw_role!r}; expected one of {BACKEND_ROLES}"
        )
    return cast(BackendRole, raw_role)


def set_backend_role(role: BackendRole) -> None:
    if role not in BACKEND_ROLES:
        raise ValueError(f"Invalid backend role: {role!r}")
    os.environ[BACKEND_ROLE_ENV] = role


def role_runs_http(role: BackendRole) -> bool:
    return role in {"api", "combined"}


def role_runs_workers(role: BackendRole) -> bool:
    return role in {"worker", "combined"}


__all__ = [
    "BACKEND_ROLE_ENV",
    "BACKEND_ROLES",
    "BackendRole",
    "get_backend_role",
    "role_runs_http",
    "role_runs_workers",
    "set_backend_role",
]
