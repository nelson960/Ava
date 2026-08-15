from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Request


@dataclass(frozen=True, slots=True)
class AuthenticatedClient:
    subject: str


class RequestAuthenticator(Protocol):
    async def authenticate(self, request: Request) -> AuthenticatedClient: ...


class DevelopmentLoopbackAuthenticator:
    """Allows local development only; never use this in production."""

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        self._allowed_hosts = frozenset(allowed_hosts)

    async def authenticate(self, request: Request) -> AuthenticatedClient:
        client = request.client
        host = client.host if client is not None else ""
        if host not in self._allowed_hosts:
            raise PermissionError(
                "Development Ava backend accepts requests only from configured loopback hosts"
            )
        return AuthenticatedClient(subject=f"development:{host}")
