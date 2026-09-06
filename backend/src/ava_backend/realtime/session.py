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
from typing import Literal
from uuid import uuid4

from pydantic import Field, StringConstraints, field_validator, model_validator
from typing_extensions import Annotated

from ..canvas.render_profile import CanvasRenderProfile
from ..canvas.svg_document import SvgDocumentValidator
from ..canvas.svg_workspace import (
    ActiveSvgCanvasScene,
    CanvasWorkspaceManifest,
    CanvasWorkspaceManifestScene,
)
from ..protocol import AgeBand
from .common import RealtimeProtocolModel
from .provider_session import NegotiatedRealtimeCall
from .timezone import validate_iana_timezone_id
from .websocket_v4 import RealtimeCoordinatorFactoryV4, RealtimeSessionCoordinatorV4


class AvaSessionCreateRequestV4(RealtimeProtocolModel):
    protocol_version: Literal[4]
    age_band: AgeBand = AgeBand.UNKNOWN
    timezone_id: str
    canvas_render_profile: CanvasRenderProfile | None = None

    @field_validator("timezone_id", mode="before")
    @classmethod
    def timezone_id_is_iana(cls, value: object) -> str:
        return validate_iana_timezone_id(value)


class CanvasWorkspaceManifestSceneV4(RealtimeProtocolModel):
    scene_id: Annotated[str, StringConstraints(min_length=1, max_length=96)]
    scene_revision: int = Field(ge=1, le=10_001)
    generation_id: Annotated[str, StringConstraints(min_length=1, max_length=96)]
    source_turn_id: Annotated[str, StringConstraints(min_length=1, max_length=96)]
    svg_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    manifest_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    svg_byte_length: int = Field(ge=1, le=512 * 1024)
    manifest_byte_length: int = Field(ge=1, le=64 * 1024)
    renderer_capability_version: Annotated[
        str, StringConstraints(min_length=1, max_length=64)
    ]
    validator_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    manifest_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    creation_sequence: int = Field(ge=1)
    modification_sequence: int | None = Field(default=None, ge=1)
    title: Annotated[str, StringConstraints(max_length=160)]
    summary: Annotated[str, StringConstraints(max_length=800)]

    def to_workspace_scene(self) -> CanvasWorkspaceManifestScene:
        return CanvasWorkspaceManifestScene(**self.model_dump())


class CanvasWorkspaceManifestV4(RealtimeProtocolModel):
    protocol_version: Literal[4]
    workspace_epoch: int = Field(ge=0)
    workspace_revision: int = Field(ge=0)
    next_creation_sequence: int = Field(ge=1)
    next_modification_sequence: int = Field(ge=1)
    focused_scene_id: (
        Annotated[str, StringConstraints(min_length=1, max_length=96)] | None
    ) = None
    scenes: list[CanvasWorkspaceManifestSceneV4] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_high_water_marks(self) -> CanvasWorkspaceManifestV4:
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("canvas workspace scene IDs must be unique")
        if self.focused_scene_id is not None and self.focused_scene_id not in scene_ids:
            raise ValueError("focusedSceneId must identify a retained scene")
        creation = [scene.creation_sequence for scene in self.scenes]
        if len(creation) != len(set(creation)) or self.next_creation_sequence <= max(
            creation, default=0
        ):
            raise ValueError("canvas workspace creation sequences are inconsistent")
        if creation != sorted(creation):
            raise ValueError("canvas workspace order must follow creationSequence")
        modification = [
            scene.modification_sequence
            for scene in self.scenes
            if scene.modification_sequence is not None
        ]
        if len(modification) != len(
            set(modification)
        ) or self.next_modification_sequence <= max(modification, default=0):
            raise ValueError("canvas workspace modification sequences are inconsistent")
        return self

    def to_workspace_manifest(self) -> CanvasWorkspaceManifest:
        return CanvasWorkspaceManifest(
            protocol_version=self.protocol_version,
            workspace_epoch=self.workspace_epoch,
            workspace_revision=self.workspace_revision,
            next_creation_sequence=self.next_creation_sequence,
            next_modification_sequence=self.next_modification_sequence,
            focused_scene_id=self.focused_scene_id,
            scenes=tuple(scene.to_workspace_scene() for scene in self.scenes),
        )


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
    canvas_token: str
    expires_at: datetime


@dataclass(slots=True)
class _RealtimeSessionRecord:
    session_id: str
    subject: str
    token_digest: bytes
    webrtc_token_digest: bytes
    canvas_token_digest: bytes
    expires_at: datetime
    coordinator: RealtimeSessionCoordinatorV4
    negotiation_started: bool = False
    negotiated_call_id: str | None = None
    claimed: bool = False
    active_deadline: datetime | None = None
    canvas_restored: bool = False
    canvas_workspace_synchronized: bool = False


@dataclass(frozen=True, slots=True)
class _VerifiedToken:
    session_id: str
    expires_at_epoch: int
    scope: Literal["events", "webrtc", "canvas"]


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
        scope: Literal["events", "webrtc", "canvas"],
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
        expected_scope: Literal["events", "webrtc", "canvas"],
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
            or payload["scope"] not in {"events", "webrtc", "canvas"}
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
    """Bounded owner of one-use protocol-v4 conversation sessions."""

    def __init__(
        self,
        *,
        coordinator_factory: RealtimeCoordinatorFactoryV4,
        token_codec: RealtimeSessionTokenCodec,
        ttl: timedelta,
        active_session_ttl: timedelta,
        max_sessions: int,
        canvas_restore_validator: SvgDocumentValidator | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_id_factory: Callable[[], str] = lambda: f"session-{uuid4()}",
    ) -> None:
        if ttl < timedelta(seconds=30) or ttl > timedelta(minutes=10):
            raise ValueError("Realtime session TTL must be between 30 and 600 seconds")
        if active_session_ttl <= timedelta(0) or active_session_ttl > timedelta(
            hours=2
        ):
            raise ValueError(
                "Realtime active session TTL must be greater than 0 and at most "
                "7200 seconds"
            )
        if not 1 <= max_sessions <= 10_000:
            raise ValueError("Realtime max sessions must be between 1 and 10000")
        self._coordinator_factory = coordinator_factory
        self._token_codec = token_codec
        self._ttl = ttl
        self._active_session_ttl = active_session_ttl
        self._max_sessions = max_sessions
        self._canvas_restore_validator = (
            canvas_restore_validator or SvgDocumentValidator()
        )
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
        timezone_id: str,
        canvas_render_profile: CanvasRenderProfile | None = None,
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
            canvas_token = self._token_codec.issue(
                session_id=session_id,
                expires_at=expires_at + self._active_session_ttl,
                scope="canvas",
            )
            coordinator = self._coordinator_factory.create(
                session_id=session_id,
                request_id=request_id,
                subject=subject,
                age_band=age_band,
                timezone_id=timezone_id,
                canvas_render_profile=canvas_render_profile,
            )
            self._records[session_id] = _RealtimeSessionRecord(
                session_id=session_id,
                subject=subject,
                token_digest=hashlib.sha256(token.encode("utf-8")).digest(),
                webrtc_token_digest=hashlib.sha256(
                    webrtc_token.encode("utf-8")
                ).digest(),
                canvas_token_digest=hashlib.sha256(
                    canvas_token.encode("utf-8")
                ).digest(),
                expires_at=expires_at,
                coordinator=coordinator,
            )
            return CreatedRealtimeSession(
                session_id=session_id,
                token=token,
                webrtc_token=webrtc_token,
                canvas_token=canvas_token,
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

    async def restore_focused_canvas(
        self,
        *,
        session_id: str,
        token: str,
        subject: str,
        generation_id: str,
        source_turn_id: str,
        scene_id: str,
        scene_revision: int,
        expected_svg_sha256: str,
        expected_manifest_sha256: str,
        svg_bytes: bytes,
    ) -> ActiveSvgCanvasScene:
        """Revalidate and hydrate one focused local scene before SDP negotiation."""

        now = self._now()
        self._token_codec.verify(
            token,
            expected_session_id=session_id,
            expected_scope="canvas",
            now=now,
        )
        token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        async with self._lock:
            record = self._require_canvas_restore_owner(
                session_id=session_id,
                subject=subject,
                token_digest=token_digest,
                now=now,
            )

        try:
            svg_text = svg_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("restored Canvas SVG must be strict UTF-8") from error
        document = self._canvas_restore_validator.validate(svg_text)
        if (
            document.svg_sha256 != expected_svg_sha256
            or document.manifest_sha256 != expected_manifest_sha256
        ):
            raise ValueError("restored Canvas hashes do not match validation output")
        scene = ActiveSvgCanvasScene(
            generation_id=generation_id,
            source_turn_id=source_turn_id,
            scene_id=scene_id,
            revision=scene_revision,
            document=document,
            first_rendered_sample=0,
        )
        async with self._lock:
            record = self._require_canvas_restore_owner(
                session_id=session_id,
                subject=subject,
                token_digest=token_digest,
                now=self._now(),
            )
            record.coordinator.restore_focused_canvas(scene)
            record.canvas_restored = True
        return scene

    async def synchronize_canvas_workspace(
        self,
        *,
        session_id: str,
        token: str,
        subject: str,
        manifest: CanvasWorkspaceManifestV4,
    ) -> bool:
        """Authenticate and adopt the ordered recovery manifest pre-negotiation."""

        now = self._now()
        self._token_codec.verify(
            token,
            expected_session_id=session_id,
            expected_scope="canvas",
            now=now,
        )
        token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        async with self._lock:
            record = self._require_canvas_restore_owner(
                session_id=session_id,
                subject=subject,
                token_digest=token_digest,
                now=now,
            )
            adopted = record.coordinator.synchronize_canvas_workspace(
                manifest.to_workspace_manifest()
            )
            record.canvas_workspace_synchronized = True
            return adopted

    def _require_canvas_restore_owner(
        self,
        *,
        session_id: str,
        subject: str,
        token_digest: bytes,
        now: datetime,
    ) -> _RealtimeSessionRecord:
        record = self._records.get(session_id)
        if record is None:
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.UNKNOWN)
        if record.expires_at <= now:
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.EXPIRED)
        if not hmac.compare_digest(
            record.canvas_token_digest, token_digest
        ) or not hmac.compare_digest(
            record.subject.encode("utf-8"), subject.encode("utf-8")
        ):
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.INVALID)
        if record.negotiation_started or record.negotiated_call_id is not None:
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.CLAIMED)
        if record.canvas_restored:
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.CLAIMED)
        return record

    async def focus_canvas(
        self,
        *,
        focus_revision: int,
        session_id: str,
        token: str,
        subject: str,
        generation_id: str,
        source_turn_id: str,
        scene_id: str,
        scene_revision: int,
        expected_svg_sha256: str,
        expected_manifest_sha256: str,
        svg_bytes: bytes,
    ) -> ActiveSvgCanvasScene:
        """Revalidate and focus one retained scene on the active session."""

        now = self._now()
        self._token_codec.verify(
            token,
            expected_session_id=session_id,
            expected_scope="canvas",
            now=now,
        )
        token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        async with self._lock:
            self._require_canvas_focus_owner(
                session_id=session_id,
                subject=subject,
                token_digest=token_digest,
                now=now,
            )

        try:
            svg_text = svg_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("focused Canvas SVG must be strict UTF-8") from error
        document = self._canvas_restore_validator.validate(svg_text)
        if (
            document.svg_sha256 != expected_svg_sha256
            or document.manifest_sha256 != expected_manifest_sha256
        ):
            raise ValueError("focused Canvas hashes do not match validation output")
        scene = ActiveSvgCanvasScene(
            generation_id=generation_id,
            source_turn_id=source_turn_id,
            scene_id=scene_id,
            revision=scene_revision,
            document=document,
            first_rendered_sample=0,
        )
        async with self._lock:
            record = self._require_canvas_focus_owner(
                session_id=session_id,
                subject=subject,
                token_digest=token_digest,
                now=self._now(),
            )
            coordinator = record.coordinator

        await coordinator.focus_canvas(scene, focus_revision=focus_revision)

        async with self._lock:
            current_record = self._records.get(session_id)
            if (
                current_record is None
                or current_record is not record
                or current_record.coordinator is not coordinator
            ):
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.UNKNOWN
                )
        return scene

    def _require_canvas_focus_owner(
        self,
        *,
        session_id: str,
        subject: str,
        token_digest: bytes,
        now: datetime,
    ) -> _RealtimeSessionRecord:
        record = self._records.get(session_id)
        if record is None:
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.UNKNOWN)
        if record.claimed:
            if record.active_deadline is None:
                raise RuntimeError("Claimed realtime session has no active deadline")
            if record.active_deadline <= now:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.EXPIRED
                )
            if record.negotiated_call_id is None:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.NOT_READY
                )
        elif record.expires_at <= now:
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.EXPIRED)
        if not hmac.compare_digest(
            record.canvas_token_digest, token_digest
        ) or not hmac.compare_digest(
            record.subject.encode("utf-8"), subject.encode("utf-8")
        ):
            raise RealtimeSessionAuthenticationError(RealtimeSessionAuthFailure.INVALID)
        return record

    async def claim(
        self,
        *,
        session_id: str,
        token: str,
    ) -> RealtimeSessionCoordinatorV4:
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
            record.active_deadline = now + self._active_session_ttl
            return record.coordinator

    async def authorize_canvas_document(
        self,
        *,
        session_id: str,
        token: str,
        subject: str,
    ) -> None:
        """Authorize an independent SVG/manifest fetch without claiming events."""

        now = self._now()
        self._token_codec.verify(
            token,
            expected_session_id=session_id,
            expected_scope="canvas",
            now=now,
        )
        token_digest = hashlib.sha256(token.encode("utf-8")).digest()
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.UNKNOWN
                )
            if record.claimed:
                if record.active_deadline is None:
                    raise RuntimeError(
                        "Claimed realtime session has no active deadline"
                    )
                if record.active_deadline < now:
                    raise RealtimeSessionAuthenticationError(
                        RealtimeSessionAuthFailure.EXPIRED
                    )
            elif record.expires_at <= now:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.EXPIRED
                )
            if not hmac.compare_digest(record.canvas_token_digest, token_digest):
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
            if record.negotiated_call_id is None:
                raise RealtimeSessionAuthenticationError(
                    RealtimeSessionAuthFailure.NOT_READY
                )

    async def release(self, session_id: str) -> None:
        async with self._lock:
            record = self._records.pop(session_id, None)
        if record is not None:
            await record.coordinator.close()

    async def notify_memory_reset(
        self,
        *,
        subject: str,
        reset_revision: int,
    ) -> None:
        async with self._lock:
            coordinators = tuple(
                record.coordinator
                for record in self._records.values()
                if record.subject == subject
            )
        await asyncio.gather(
            *(
                coordinator.memory_reset(reset_revision=reset_revision)
                for coordinator in coordinators
            )
        )

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
