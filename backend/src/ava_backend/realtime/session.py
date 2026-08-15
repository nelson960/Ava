from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol
from uuid import uuid4

from ..protocol import AgeBand
from .common import RealtimeProtocolModel
from .coordinator import RealtimeSessionCoordinator
from .provider_session import NegotiatedRealtimeCall


class AvaSessionCreateRequest(RealtimeProtocolModel):
    protocol_version: Literal[3]
    age_band: AgeBand = AgeBand.UNKNOWN


class RealtimeCoordinatorFactory(Protocol):
    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> RealtimeSessionCoordinator: ...


class RealtimeSessionCapacityError(RuntimeError):
    """Raised when active unexpired sessions reach the configured bound."""


class RealtimeSessionAuthFailure(StrEnum):
    INVALID = "invalid"
    EXPIRED = "expired"
    UNKNOWN = "unknown"
    CLAIMED = "claimed"
    NOT_READY = "notReady"


class RealtimeSessionAuthenticationError(PermissionError):
    def __init__(self, failure: RealtimeSessionAuthFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class CreatedRealtimeSession:
    session_id: str
    token: str
    webrtc_token: str
    expires_at: datetime


@dataclass(slots=True)
class _RealtimeSessionRecord:
    session_id: str
    subject: str
    token_digest: bytes
    webrtc_token_digest: bytes
    expires_at: datetime
    coordinator: RealtimeSessionCoordinator
    negotiation_started: bool = False
    negotiated_call_id: str | None = None
    claimed: bool = False


@dataclass(frozen=True, slots=True)
class _VerifiedToken:
    session_id: str
    expires_at_epoch: int
    scope: Literal["events", "webrtc"]


class RealtimeSessionTokenCodec:
    """HMAC-authenticated, short-lived token containing no child data."""

    _token_pattern = re.compile(r"^[A-Za-z0-9_-]{16,2048}\.[A-Za-z0-9_-]{32,128}$")

    def __init__(self, secret: str) -> None:
        normalized = secret.strip()
        if len(normalized) < 32:
            raise ValueError("Realtime session token secret must contain 32 characters")
        self._secret = normalized.encode("utf-8")

    def issue(
        self,
        *,
        session_id: str,
        expires_at: datetime,
        scope: Literal["events", "webrtc"],
    ) -> str:
        if expires_at.tzinfo is None:
            raise ValueError("Realtime session expiry must be timezone-aware")
        payload = json.dumps(
            {
                "exp": int(expires_at.timestamp()),
                "scope": scope,
                "sid": session_id,
                "v": 1,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded_payload = _base64url_encode(payload)
        signature = hmac.new(
            self._secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded_payload}.{_base64url_encode(signature)}"

    def verify(
        self,
        token: str,
        *,
        expected_session_id: str,
        expected_scope: Literal["events", "webrtc"],
        now: datetime,
    ) -> _VerifiedToken:
        if now.tzinfo is None:
            raise ValueError("Realtime token verification time must be timezone-aware")
        normalized = token.strip()
        if not self._token_pattern.fullmatch(normalized):
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.INVALID)
        encoded_payload, encoded_signature = normalized.split(".", maxsplit=1)
        expected_signature = hmac.new(
            self._secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        try:
            supplied_signature = _base64url_decode(encoded_signature)
        except ValueError as error:
            raise RealtimeSessionAuthenticationError(
                RealtimeSessionAuthFailure.INVALID
            ) from error
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.INVALID)
        try:
            raw_payload = _base64url_decode(encoded_payload)
            payload = json.loads(raw_payload)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RealtimeSessionAuthenticationError(
                RealtimeSessionAuthFailure.INVALID
            ) from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"exp", "scope", "sid", "v"}
            or payload["v"] != 1
            or not isinstance(payload["exp"], int)
            or payload["scope"] not in {"events", "webrtc"}
            or not isinstance(payload["sid"], str)
            or payload["sid"] != expected_session_id
            or payload["scope"] != expected_scope
        ):
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.INVALID)
        if payload["exp"] <= int(now.timestamp()):
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.EXPIRED)
        return _VerifiedToken(
            session_id=payload["sid"],
            expires_at_epoch=payload["exp"],
            scope=payload["scope"],
        )


class RealtimeSessionRegistry:
    """Bounded owner of one-use protocol-v3 coordinator sessions."""

    def __init__(
        self,
        *,
        coordinator_factory: RealtimeCoordinatorFactory,
        token_codec: RealtimeSessionTokenCodec,
        ttl: timedelta,
        max_sessions: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_id_factory: Callable[[], str] = lambda: f"session-{uuid4()}",
    ) -> None:
        if ttl < timedelta(seconds=30) or ttl > timedelta(minutes=10):
            raise ValueError("Realtime session TTL must be between 30 and 600 seconds")
        if not 1 <= max_sessions <= 10_000:
            raise ValueError("Realtime max sessions must be between 1 and 10000")
        self._coordinator_factory = coordinator_factory
        self._token_codec = token_codec
        self._ttl = ttl
        self._max_sessions = max_sessions
        self._now = now
        self._session_id_factory = session_id_factory
        self._records: dict[str, _RealtimeSessionRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def active_session_count(self) -> int:
        return len(self._records)

    async def create(
        self,
        *,
        request_id: str,
        subject: str,
        age_band: AgeBand,
    ) -> CreatedRealtimeSession:
        await self.purge_expired()
        async with self._lock:
            if len(self._records) >= self._max_sessions:
                raise RealtimeSessionCapacityError(
                    "Realtime session capacity is exhausted"
                )
            session_id = self._session_id_factory()
            if not re.fullmatch(
                r"^[A-Za-z][A-Za-z0-9._:-]{0,63}$",
                session_id,
            ):
                raise RuntimeError(
                    "Realtime session id factory returned an invalid identifier"
                )
            if session_id in self._records:
                raise RuntimeError("Realtime session id factory returned a duplicate")
            expires_at = self._now() + self._ttl
            token = self._token_codec.issue(
                session_id=session_id,
                expires_at=expires_at,
                scope="events",
            )
            webrtc_token = self._token_codec.issue(
                session_id=session_id,
                expires_at=expires_at,
                scope="webrtc",
            )
            coordinator = self._coordinator_factory.create(
                session_id=session_id,
                request_id=request_id,
                subject=subject,
                age_band=age_band,
            )
            self._records[session_id] = _RealtimeSessionRecord(
                session_id=session_id,
                subject=subject,
                token_digest=hashlib.sha256(token.encode("utf-8")).digest(),
                webrtc_token_digest=hashlib.sha256(
                    webrtc_token.encode("utf-8")
                ).digest(),
                expires_at=expires_at,
                coordinator=coordinator,
            )
            return CreatedRealtimeSession(
                session_id=session_id,
                token=token,
                webrtc_token=webrtc_token,
                expires_at=expires_at,
            )

    async def negotiate(
        self,
        *,
        session_id: str,
        token: str,
        subject: str,
        offer_sdp: str,
    ) -> NegotiatedRealtimeCall:
        now = self._now()
        self._token_codec.verify(
            token,
            expected_session_id=session_id,
            expected_scope="webrtc",
            now=now,
        )
        token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.UNKNOWN
                )
            if record.expires_at <= now:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.EXPIRED
                )
            if not hmac.compare_digest(record.webrtc_token_digest, token_digest):
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.INVALID
                )
            if not hmac.compare_digest(
                record.subject.encode("utf-8"),
                subject.encode("utf-8"),
            ):
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.INVALID
                )
            if record.negotiation_started:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.CLAIMED
                )
            record.negotiation_started = True
            coordinator = record.coordinator

        try:
            negotiated = await coordinator.negotiate(offer_sdp=offer_sdp)
        except Exception:
            await self.release(session_id)
            raise

        async with self._lock:
            current = self._records.get(session_id)
            if current is None or current.coordinator is not coordinator:
                raise RuntimeError(
                    "Realtime session was released during SDP negotiation"
                )
            current.negotiated_call_id = negotiated.provider_call_id
        return negotiated

    async def claim(
        self,
        *,
        session_id: str,
        token: str,
    ) -> RealtimeSessionCoordinator:
        now = self._now()
        self._token_codec.verify(
            token,
            expected_session_id=session_id,
            expected_scope="events",
            now=now,
        )
        token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.UNKNOWN
                )
            if record.expires_at <= now:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.EXPIRED
                )
            if not hmac.compare_digest(record.token_digest, token_digest):
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.INVALID
                )
            if record.negotiated_call_id is None:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.NOT_READY
                )
            if record.claimed:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.CLAIMED
                )
            record.claimed = True
            return record.coordinator

    async def release(self, session_id: str) -> None:
        async with self._lock:
            record = self._records.pop(session_id, None)
        if record is not None:
            await record.coordinator.close()

    async def purge_expired(self) -> int:
        now = self._now()
        async with self._lock:
            expired = [
                self._records.pop(session_id)
                for session_id, record in tuple(self._records.items())
                if not record.claimed and record.expires_at <= now
            ]
        for record in expired:
            await record.coordinator.close()
        return len(expired)

    async def close(self) -> None:
        async with self._lock:
            records = list(self._records.values())
            self._records.clear()
        for record in records:
            await record.coordinator.close()


def ephemeral_development_token_secret() -> str:
    """Generate a process-local secret; production configuration must supply one."""

    return secrets.token_urlsafe(48)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url value")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid base64url value") from error
