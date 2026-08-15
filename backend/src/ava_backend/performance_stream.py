from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator

from .memory import (
    ConversationMemoryStore,
    ConversationTurn,
    MemoryStoreError,
)
from .errors import (
    AvaBackendError,
    ProviderRefusalError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SpeechProviderResponseError,
    SpeechProviderTimeoutError,
    SpeechProviderUnavailableError,
)
from .orchestration import AvaPerformanceOrchestrator
from .protocol import (
    AudioChunkEvent,
    AudioMetadataEvent,
    CharacterTiming,
    CharacterTimingBatchEvent,
    ConversationMessage,
    ConversationRole,
    DiagramCueEvent,
    LessonTurnRequest,
    PerformanceEvent,
    PerformanceFailure,
    PerformanceFailureCode,
    PerformancePlanEvent,
    SegmentBoundaryEvent,
    TurnAcceptedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
)
from .providers.base import AvaTimedSpeech

LOGGER = logging.getLogger("ava_backend.performance")


class AvaPerformanceStream:
    """Composes one validated model plan with sequential timed speech.

    TTS runs per semantic segment so the app receives an exact segment boundary
    for captions and dashboard cues. ``previous_text`` and ``next_text`` keep
    provider prosody continuous without merging dashboard timing into model
    guesses.
    """

    def __init__(
        self,
        orchestrator: AvaPerformanceOrchestrator,
        speech: AvaTimedSpeech,
        *,
        memory: ConversationMemoryStore | None = None,
        memory_turn_limit: int = 6,
    ) -> None:
        if not 0 <= memory_turn_limit <= 8:
            raise ValueError("memory_turn_limit must be between 0 and 8")
        self._orchestrator = orchestrator
        self._speech = speech
        self._memory = memory
        self._memory_turn_limit = memory_turn_limit

    async def stream(
        self,
        *,
        request: LessonTurnRequest,
        turn_id: str,
        request_id: str,
        subject: str,
    ) -> AsyncIterator[PerformanceEvent]:
        sequence = 0
        yield TurnAcceptedEvent(
            sequence=sequence,
            turn_id=turn_id,
            request_id=request_id,
        )
        sequence += 1

        try:
            effective_request = request
            if self._memory is not None:
                memory_turns = await self._memory.load_recent_turns(
                    subject=subject,
                    limit=self._memory_turn_limit,
                )
                recent_messages = [
                    message
                    for turn in memory_turns
                    for message in (
                        ConversationMessage(
                            role=ConversationRole.CHILD,
                            text=turn.child_text,
                        ),
                        ConversationMessage(
                            role=ConversationRole.AVA,
                            text=turn.ava_text,
                        ),
                    )
                ]
                effective_request = request.model_copy(
                    update={"recent_messages": recent_messages}
                )

            outcome = await self._orchestrator.create_performance(effective_request)
            plan = outcome.performance
            yield PerformancePlanEvent(
                sequence=sequence,
                turn_id=turn_id,
                performance=plan,
            )
            sequence += 1

            metadata = self._speech.metadata
            yield AudioMetadataEvent(
                sequence=sequence,
                turn_id=turn_id,
                audio_format=metadata.audio_format,
                media_type=metadata.media_type,
                sample_rate_hz=metadata.sample_rate_hz,
                channels=metadata.channels,
                bits_per_sample=metadata.bits_per_sample,
                model=metadata.model,
                voice_id=metadata.voice_id,
                voice_label=metadata.voice_label,
            )
            sequence += 1

            audio_chunk_count = 0
            for segment_index, segment in enumerate(plan.segments):
                yield SegmentBoundaryEvent(
                    sequence=sequence,
                    turn_id=turn_id,
                    segment_id=segment.id,
                    segment_index=segment_index,
                )
                sequence += 1
                if segment.diagram_cue is not None:
                    yield DiagramCueEvent(
                        sequence=sequence,
                        turn_id=turn_id,
                        segment_id=segment.id,
                        cue=segment.diagram_cue,
                    )
                    sequence += 1

                previous_text = (
                    plan.segments[segment_index - 1].speech
                    if segment_index > 0
                    else None
                )
                next_text = (
                    plan.segments[segment_index + 1].speech
                    if segment_index + 1 < len(plan.segments)
                    else None
                )
                chunk_index = 0
                async for chunk in self._speech.stream(
                    text=segment.speech,
                    previous_text=previous_text,
                    next_text=next_text,
                ):
                    if chunk.alignment:
                        yield CharacterTimingBatchEvent(
                            sequence=sequence,
                            turn_id=turn_id,
                            segment_id=segment.id,
                            chunk_index=chunk_index,
                            timings=[
                                CharacterTiming(
                                    character=timing.character,
                                    start_seconds=timing.start_seconds,
                                    end_seconds=timing.end_seconds,
                                )
                                for timing in chunk.alignment
                            ],
                        )
                        sequence += 1

                    yield AudioChunkEvent(
                        sequence=sequence,
                        turn_id=turn_id,
                        segment_id=segment.id,
                        chunk_index=chunk_index,
                        audio_base64=base64.b64encode(chunk.audio).decode("ascii"),
                    )
                    sequence += 1
                    chunk_index += 1
                    audio_chunk_count += 1

            if self._memory is not None:
                await self._memory.append_turn(
                    subject=subject,
                    turn=ConversationTurn(
                        child_text=request.message,
                        ava_text=plan.speech,
                    ),
                )

            yield TurnCompletedEvent(
                sequence=sequence,
                turn_id=turn_id,
                audio_chunk_count=audio_chunk_count,
            )
        except MemoryStoreError:
            LOGGER.warning(
                "performance_memory_failed request_id=%s turn_id=%s",
                request_id,
                turn_id,
            )
            yield TurnFailedEvent(
                sequence=sequence,
                turn_id=turn_id,
                failure=PerformanceFailure(
                    code=PerformanceFailureCode.MEMORY_UNAVAILABLE,
                    message="Ava's conversation memory is temporarily unavailable.",
                    retryable=True,
                ),
            )
        except AvaBackendError as error:
            yield TurnFailedEvent(
                sequence=sequence,
                turn_id=turn_id,
                failure=_provider_failure(error),
            )
        except Exception:
            LOGGER.exception(
                "performance_stream_failed request_id=%s turn_id=%s",
                request_id,
                turn_id,
            )
            yield TurnFailedEvent(
                sequence=sequence,
                turn_id=turn_id,
                failure=PerformanceFailure(
                    code=PerformanceFailureCode.INTERNAL_ERROR,
                    message="Ava could not complete the performance stream.",
                    retryable=False,
                ),
            )


def _provider_failure(error: AvaBackendError) -> PerformanceFailure:
    if isinstance(error, ProviderRefusalError):
        return PerformanceFailure(
            code=PerformanceFailureCode.MODEL_REFUSAL,
            message=error.public_message,
            retryable=False,
        )
    if isinstance(error, ProviderTimeoutError):
        return PerformanceFailure(
            code=PerformanceFailureCode.MODEL_TIMEOUT,
            message=error.public_message,
            retryable=True,
        )
    if isinstance(error, ProviderResponseError):
        return PerformanceFailure(
            code=PerformanceFailureCode.INVALID_PLAN,
            message=error.public_message,
            retryable=True,
        )
    if isinstance(error, ProviderUnavailableError):
        return PerformanceFailure(
            code=PerformanceFailureCode.MODEL_UNAVAILABLE,
            message=error.public_message,
            retryable=True,
        )
    if isinstance(error, SpeechProviderTimeoutError):
        return PerformanceFailure(
            code=PerformanceFailureCode.SPEECH_TIMEOUT,
            message=error.public_message,
            retryable=True,
        )
    if isinstance(error, SpeechProviderResponseError):
        return PerformanceFailure(
            code=PerformanceFailureCode.TIMING_INVALID,
            message=error.public_message,
            retryable=True,
        )
    if isinstance(error, SpeechProviderUnavailableError):
        return PerformanceFailure(
            code=PerformanceFailureCode.SPEECH_UNAVAILABLE,
            message=error.public_message,
            retryable=True,
        )
    return PerformanceFailure(
        code=PerformanceFailureCode.INTERNAL_ERROR,
        message="Ava could not complete the performance stream.",
        retryable=False,
    )
