from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Annotated, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, StringConstraints, TypeAdapter

from .config import Settings
from .protocol import NonEmptyText, ProtocolModel


class ConversationTurn(ProtocolModel):
    child_text: Annotated[NonEmptyText, StringConstraints(max_length=2000)]
    ava_text: Annotated[NonEmptyText, StringConstraints(max_length=1800)]


class ConversationMemoryStore(Protocol):
    """Persistence boundary implemented by the future cloud memory adapter."""

    async def load_recent_turns(
        self,
        *,
        subject: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]: ...

    async def append_turn(
        self,
        *,
        subject: str,
        turn: ConversationTurn,
    ) -> None: ...

    async def reset_conversation(self, *, subject: str) -> None: ...


class DevelopmentConversationMemoryStore:
    """Explicit process-local store for development and contract tests only.

    The application does not select this store automatically. Production must
    inject a durable ``ConversationMemoryStore`` implementation.
    """

    def __init__(self, *, max_turns_per_subject: int = 8) -> None:
        if not 1 <= max_turns_per_subject <= 8:
            raise ValueError("max_turns_per_subject must be between 1 and 8")
        self._max_turns_per_subject = max_turns_per_subject
        self._turns: defaultdict[str, deque[ConversationTurn]] = defaultdict(
            lambda: deque(maxlen=self._max_turns_per_subject)
        )
        self._lock = asyncio.Lock()

    async def load_recent_turns(
        self,
        *,
        subject: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        normalized_subject = _validate_subject(subject)
        if not 0 <= limit <= self._max_turns_per_subject:
            raise ValueError("limit must be between 0 and max_turns_per_subject")
        if limit == 0:
            return ()

        async with self._lock:
            turns = tuple(self._turns.get(normalized_subject, ()))
            return tuple(turn.model_copy(deep=True) for turn in turns[-limit:])

    async def append_turn(
        self,
        *,
        subject: str,
        turn: ConversationTurn,
    ) -> None:
        normalized_subject = _validate_subject(subject)
        validated_turn = ConversationTurn.model_validate(turn.model_dump())
        async with self._lock:
            self._turns[normalized_subject].append(validated_turn)

    async def reset_conversation(self, *, subject: str) -> None:
        normalized_subject = _validate_subject(subject)
        async with self._lock:
            self._turns.pop(normalized_subject, None)


class MemoryStoreError(RuntimeError):
    """Raised when durable memory cannot complete a validated operation."""


class _MemoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    child_text: str
    ava_text: str


_MEMORY_ROWS = TypeAdapter(list[_MemoryRow])


class SupabaseConversationMemoryStore:
    """Strict server-only PostgREST adapter for bounded conversation turns."""

    _maximum_response_bytes = 64 * 1024

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        origin, service_role_key = settings.require_supabase_memory_credentials()
        self._endpoint = f"{origin}/rest/v1/ava_conversation_turns"
        self._headers = {
            "apikey": service_role_key,
            "authorization": f"Bearer {service_role_key}",
            "content-type": "application/json",
        }
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.supabase_timeout_seconds)
        )

    async def load_recent_turns(
        self,
        *,
        subject: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        normalized_subject = _validate_subject(subject)
        if not 0 <= limit <= 8:
            raise ValueError("limit must be between 0 and 8")
        if limit == 0:
            return ()

        response = await self._request(
            "GET",
            params={
                "select": "child_text,ava_text",
                "subject": f"eq.{normalized_subject}",
                "order": "created_at.desc,id.desc",
                "limit": str(limit),
            },
        )
        try:
            parsed = _MEMORY_ROWS.validate_json(response.content)
        except (ValueError, TypeError) as error:
            raise MemoryStoreError(
                "Supabase memory returned invalid turn data"
            ) from error
        parsed.reverse()
        return tuple(
            ConversationTurn(
                child_text=row.child_text,
                ava_text=row.ava_text,
            )
            for row in parsed
        )

    async def append_turn(
        self,
        *,
        subject: str,
        turn: ConversationTurn,
    ) -> None:
        normalized_subject = _validate_subject(subject)
        validated_turn = ConversationTurn.model_validate(turn.model_dump())
        await self._request(
            "POST",
            headers={"prefer": "return=minimal"},
            json={
                "subject": normalized_subject,
                "child_text": validated_turn.child_text,
                "ava_text": validated_turn.ava_text,
            },
            expected_status=201,
        )

    async def reset_conversation(self, *, subject: str) -> None:
        normalized_subject = _validate_subject(subject)
        await self._request(
            "DELETE",
            params={"subject": f"eq.{normalized_subject}"},
            headers={"prefer": "return=minimal"},
            expected_status=204,
        )

    async def _request(
        self,
        method: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        expected_status: int = 200,
    ) -> httpx.Response:
        request_headers = dict(self._headers)
        if headers is not None:
            request_headers.update(headers)
        try:
            response = await self._client.request(
                method,
                self._endpoint,
                params=params,
                headers=request_headers,
                json=json,
            )
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise MemoryStoreError(
                "Supabase conversation memory is unavailable"
            ) from error
        if response.status_code != expected_status:
            raise MemoryStoreError(
                "Supabase conversation memory rejected the operation "
                f"with HTTP {response.status_code}"
            )
        if len(response.content) > self._maximum_response_bytes:
            raise MemoryStoreError(
                "Supabase conversation memory response exceeded 64 KiB"
            )
        return response


def _validate_subject(subject: str) -> str:
    if not subject or subject != subject.strip():
        raise ValueError(
            "memory subject must be non-empty and have no outer whitespace"
        )
    if len(subject) > 256:
        raise ValueError("memory subject cannot exceed 256 characters")
    if any(
        character
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:@-"
        for character in subject
    ):
        raise ValueError("memory subject contains unsupported characters")
    return subject
