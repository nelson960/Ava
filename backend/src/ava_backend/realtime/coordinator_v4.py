from __future__ import annotations

import asyncio

from ..canvas.svg_delivery import StoredSvgCanvasDocument
from ..canvas.svg_document import (
    SVG_MANIFEST_VERSION,
    SVG_RENDERER_CAPABILITY_VERSION,
    SVG_VALIDATOR_VERSION,
)
from ..canvas.svg_session import (
    ActiveSvgCanvasScene,
    SvgCanvasCandidate,
    SvgCanvasPresentationCue,
    SvgCanvasStateError,
)
from .events_v4 import (
    CanvasDocumentDiscardReason,
    CanvasGenerationRejectionCode,
    CanvasInteractionEventV4,
)
from .svg_canvas_jobs import SvgCanvasJobEventSink


class ProtocolV4CanvasEventSink(SvgCanvasJobEventSink):
    """Bounded canvas-control queue; exact SVG bytes stay in the HTTP store."""

    def __init__(self, *, maximum_events: int = 64) -> None:
        if not 8 <= maximum_events <= 256:
            raise ValueError("v4 canvas event capacity must be between 8 and 256")
        self._events: asyncio.Queue[dict[str, object]] = asyncio.Queue(
            maxsize=maximum_events
        )

    async def next_payload(self) -> dict[str, object]:
        return await self._events.get()

    async def generation_started(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
    ) -> None:
        await self._put(
            {
                "event": "canvasGenerationStarted",
                "generationId": generation_id,
                "sourceTurnId": source_turn_id,
            }
        )

    async def document_available(self, stored: StoredSvgCanvasDocument) -> None:
        document = stored.document
        await self._put(
            {
                "event": "canvasDocumentAvailable",
                "generationId": stored.generation_id,
                "sourceTurnId": stored.source_turn_id,
                "sceneId": stored.scene_id,
                "sceneRevision": stored.scene_revision,
                "svgPath": stored.svg_path,
                "manifestPath": stored.manifest_path,
                "svgSha256": document.svg_sha256,
                "manifestSha256": document.manifest_sha256,
                "svgByteLength": len(document.svg_bytes),
                "manifestByteLength": len(document.manifest_bytes),
                "rendererCapabilityVersion": SVG_RENDERER_CAPABILITY_VERSION,
                "validatorVersion": SVG_VALIDATOR_VERSION,
                "manifestVersion": SVG_MANIFEST_VERSION,
            }
        )

    async def presentation_cue(
        self,
        candidate: SvgCanvasCandidate,
        cue: SvgCanvasPresentationCue,
    ) -> None:
        await self._put(
            {
                "event": "canvasPresentationCue",
                "generationId": cue.generation_id,
                "sourceTurnId": candidate.source_turn_id,
                "sceneId": cue.scene_id,
                "sceneRevision": cue.revision,
                "presentationSampleOffset": cue.presentation_sample_offset,
                "svgSha256": cue.svg_sha256,
                "manifestSha256": cue.manifest_sha256,
            }
        )

    async def activated(self, active: ActiveSvgCanvasScene) -> None:
        await self._put(
            {
                "event": "canvasActivated",
                "generationId": active.generation_id,
                "sourceTurnId": active.source_turn_id,
                "sceneId": active.scene_id,
                "sceneRevision": active.revision,
                "firstRenderedSample": active.first_rendered_sample,
                "svgSha256": active.document.svg_sha256,
                "manifestSha256": active.document.manifest_sha256,
            }
        )

    async def document_discarded(
        self,
        candidate: SvgCanvasCandidate,
        reason: CanvasDocumentDiscardReason,
    ) -> None:
        await self._discarded(
            generation_id=candidate.generation_id,
            source_turn_id=candidate.source_turn_id,
            scene_id=candidate.scene_id,
            scene_revision=candidate.revision,
            reason=reason,
        )

    async def active_document_discarded(
        self,
        active: ActiveSvgCanvasScene,
        reason: CanvasDocumentDiscardReason,
    ) -> None:
        await self._discarded(
            generation_id=active.generation_id,
            source_turn_id=active.source_turn_id,
            scene_id=active.scene_id,
            scene_revision=active.revision,
            reason=reason,
        )

    async def generation_rejected(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
        code: CanvasGenerationRejectionCode,
        message: str,
        retryable: bool,
    ) -> None:
        await self._put(
            {
                "event": "canvasRejected",
                "generationId": generation_id,
                "sourceTurnId": source_turn_id,
                "rejectionCode": code,
                "message": message,
                "retryable": retryable,
            }
        )

    async def tool_call_rejected(
        self,
        *,
        call_id: str,
        source_turn_id: str,
        message: str,
    ) -> None:
        await self._put(
            {
                "event": "canvasRejected",
                "callId": call_id,
                "sourceTurnId": source_turn_id,
                "rejectionCode": CanvasGenerationRejectionCode.PROVIDER_INVALID,
                "message": message[:240],
                "retryable": False,
            }
        )

    async def interaction_rejected(
        self,
        event: CanvasInteractionEventV4,
        error: SvgCanvasStateError,
    ) -> None:
        await self._put(
            {
                "event": "interactionRejected",
                "sceneId": event.scene_id,
                "sceneRevision": event.scene_revision,
                "interactionRevision": event.interaction_revision,
                "bindingId": event.binding_id,
                "rejectionCode": error.code,
                "message": str(error)[:240],
            }
        )

    async def _discarded(
        self,
        *,
        generation_id: str,
        source_turn_id: str,
        scene_id: str,
        scene_revision: int,
        reason: CanvasDocumentDiscardReason,
    ) -> None:
        await self._put(
            {
                "event": "canvasDocumentDiscarded",
                "generationId": generation_id,
                "sourceTurnId": source_turn_id,
                "sceneId": scene_id,
                "sceneRevision": scene_revision,
                "reason": reason,
            }
        )

    async def _put(self, payload: dict[str, object]) -> None:
        await self._events.put(payload)
