from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from ..protocol import AvaPerformancePlan, AvaResponse


@dataclass(frozen=True, slots=True)
class ModelAvaResult:
    response: AvaResponse
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class AvaModel(Protocol):
    async def generate_response(
        self,
        *,
        instructions: str,
        model_input: list[dict[str, str]],
        safety_id: str | None,
    ) -> ModelAvaResult: ...


@dataclass(frozen=True, slots=True)
class PerformanceModelResult:
    performance: AvaPerformancePlan
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class AvaPerformanceModel(Protocol):
    async def generate_performance(
        self,
        *,
        instructions: str,
        model_input: list[dict[str, str]],
        safety_id: str | None,
    ) -> PerformanceModelResult: ...


@dataclass(frozen=True, slots=True)
class SpeechAudioResult:
    audio: bytes
    audio_format: Literal["wav"]
    media_type: Literal["audio/wav"]
    model: str
    voice: str
    provider_request_id: str | None = None


class AvaSpeech(Protocol):
    async def synthesize(
        self,
        *,
        text: str,
        instructions: str,
    ) -> SpeechAudioResult: ...


@dataclass(frozen=True, slots=True)
class SpeechCharacterTiming:
    character: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class TimedSpeechMetadata:
    audio_format: Literal["pcm_s16le"]
    media_type: Literal["audio/x-raw"]
    sample_rate_hz: int
    channels: Literal[1]
    bits_per_sample: Literal[16]
    model: str
    voice_id: str
    voice_label: str


@dataclass(frozen=True, slots=True)
class TimedSpeechChunk:
    audio: bytes
    alignment: tuple[SpeechCharacterTiming, ...]
    provider_request_id: str | None = None


class AvaTimedSpeech(Protocol):
    @property
    def metadata(self) -> TimedSpeechMetadata: ...

    def stream(
        self,
        *,
        text: str,
        previous_text: str | None = None,
        next_text: str | None = None,
    ) -> AsyncIterator[TimedSpeechChunk]: ...
