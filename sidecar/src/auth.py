from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Iterable

from fastapi import Header, HTTPException, status


AUTH_EXEMPT_PATHS = frozenset({"/health"})


@dataclass(frozen=True)
class LocalAuthConfig:
    auth_header_value: str
    exempt_paths: frozenset[str] = AUTH_EXEMPT_PATHS


def generate_local_auth_token() -> str:
    return secrets.token_urlsafe(32)


def is_exempt_path(path: str, exempt_paths: Iterable[str] = AUTH_EXEMPT_PATHS) -> bool:
    return path in set(exempt_paths)


def validate_bearer_token(authorization: str | None, expected_token: str) -> None:
    if authorization is None or authorization.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
        )

    if not secrets.compare_digest(token.strip(), expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid auth token",
        )


def validate_authorization_header(
    authorization: str | None,
    expected_auth_header_value: str,
) -> None:
    if authorization is None or authorization.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    if not secrets.compare_digest(authorization.strip(), expected_auth_header_value.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid auth token",
        )


def auth_header_dependency(
    authorization: str | None = Header(default=None),
) -> str | None:
    return authorization
