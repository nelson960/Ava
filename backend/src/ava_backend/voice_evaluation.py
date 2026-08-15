from __future__ import annotations

import hashlib
import io
import json
import os
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from .protocol import (
    AvaDelivery,
    AvaGestureIntent,
    NonEmptyText,
    ProtocolModel,
    SegmentId,
)
from .providers.base import AvaSpeech
from .voice import VOICE_STYLE_VERSION, build_voice_instructions


class VoiceEvaluationError(RuntimeError):
    """Raised when a paid voice evaluation cannot be executed safely."""


class DurationBounds(ProtocolModel):
    minimum: float = Field(gt=0, le=60)
    maximum: float = Field(gt=0, le=60)

    @model_validator(mode="after")
    def maximum_exceeds_minimum(self) -> DurationBounds:
        if self.maximum <= self.minimum:
            raise ValueError("duration maximum must exceed minimum")
        return self


class VoiceMotionEvalCase(ProtocolModel):
    id: SegmentId
    text: Annotated[NonEmptyText, StringConstraints(max_length=4096)]
    delivery: AvaDelivery
    gesture: AvaGestureIntent
    duration_seconds: DurationBounds
    motion_review_focus: list[
        Annotated[NonEmptyText, StringConstraints(max_length=180)]
    ] = Field(min_length=2, max_length=6)


class VoiceMotionEvalSuite(ProtocolModel):
    schema_version: Literal[1]
    suite_id: SegmentId
    voice_style_version: str
    human_review_criteria: list[
        Annotated[NonEmptyText, StringConstraints(max_length=240)]
    ] = Field(min_length=4, max_length=12)
    cases: list[VoiceMotionEvalCase] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> VoiceMotionEvalSuite:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("voice-motion evaluation case IDs must be unique")
        return self


def load_voice_motion_eval_suite(path: str | Path) -> VoiceMotionEvalSuite:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VoiceEvaluationError(
            f"voice-motion evaluation suite does not exist: {source}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise VoiceEvaluationError(
            f"voice-motion evaluation suite is invalid JSON: {exc}"
        ) from exc
    suite = VoiceMotionEvalSuite.model_validate(payload)
    if suite.voice_style_version != VOICE_STYLE_VERSION:
        raise VoiceEvaluationError(
            "voice-motion suite targets "
            f"{suite.voice_style_version!r}, expected {VOICE_STYLE_VERSION!r}"
        )
    return suite


def select_voice_eval_cases(
    suite: VoiceMotionEvalSuite,
    requested_ids: list[str] | None,
) -> list[VoiceMotionEvalCase]:
    if requested_ids is None:
        return suite.cases
    if len(requested_ids) != len(set(requested_ids)):
        raise VoiceEvaluationError(
            "duplicate voice evaluation case IDs would spend credits twice"
        )
    by_id = {case.id: case for case in suite.cases}
    missing = sorted(set(requested_ids) - by_id.keys())
    if missing:
        raise VoiceEvaluationError(
            f"unknown voice evaluation case IDs: {', '.join(missing)}"
        )
    return [by_id[case_id] for case_id in requested_ids]


async def generate_voice_evaluation_batch(
    *,
    speech: AvaSpeech,
    suite_path: str | Path,
    output_directory: str | Path,
    model: str,
    voice: str,
    requested_ids: list[str] | None,
    replace: bool,
) -> dict[str, Any]:
    suite_source = Path(suite_path).resolve()
    suite = load_voice_motion_eval_suite(suite_source)
    selected = select_voice_eval_cases(suite, requested_ids)
    output = Path(output_directory).resolve()
    audio_directory = output / "audio"
    audio_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    manifest = _load_or_initialize_manifest(
        manifest_path,
        suite=suite,
        suite_sha256=_sha256(suite_source),
        model=model,
        voice=voice,
    )

    for case in selected:
        existing = next(
            (item for item in manifest["items"] if item.get("id") == case.id),
            None,
        )
        if existing is not None and not replace:
            raise VoiceEvaluationError(
                f"case {case.id!r} already exists; pass --replace to spend "
                "credits regenerating it"
            )
        audio_path = audio_directory / f"{case.id}.wav"
        if audio_path.exists() and existing is None and not replace:
            raise VoiceEvaluationError(
                f"unregistered audio already exists for {case.id!r}: {audio_path}"
            )

        instructions = build_voice_instructions(AvaDelivery(case.delivery))
        result = await speech.synthesize(
            text=case.text,
            instructions=instructions,
        )
        if result.model != model or result.voice != voice:
            raise VoiceEvaluationError(
                "speech provider result does not match the configured model and voice"
            )
        properties = inspect_wav(result.audio)
        duration = float(properties["durationSeconds"])
        duration_passed = (
            case.duration_seconds.minimum <= duration <= case.duration_seconds.maximum
        )
        temporary = audio_path.with_suffix(".wav.tmp")
        temporary.write_bytes(result.audio)
        os.replace(temporary, audio_path)
        record = {
            "id": case.id,
            "textSha256": _sha256_bytes(case.text.encode("utf-8")),
            "delivery": case.delivery,
            "gesture": case.gesture,
            "audioPath": str(audio_path.relative_to(output)),
            "audioSha256": _sha256(audio_path),
            "durationSeconds": duration,
            "durationCheck": {
                "status": "passed" if duration_passed else "failed",
                "minimumSeconds": case.duration_seconds.minimum,
                "maximumSeconds": case.duration_seconds.maximum,
            },
            "sampleRateHz": properties["sampleRateHz"],
            "channels": properties["channels"],
            "providerRequestId": result.provider_request_id,
            "voiceInstructionsSha256": _sha256_bytes(instructions.encode("utf-8")),
            "voiceReview": {"status": "pending"},
            "motionStatus": "awaiting_emage",
            "generatedAtUtc": datetime.now(UTC).isoformat(),
        }
        manifest["items"] = [
            item for item in manifest["items"] if item.get("id") != case.id
        ]
        manifest["items"].append(record)
        manifest["items"].sort(key=lambda item: str(item.get("id", "")))
        manifest["updatedAtUtc"] = datetime.now(UTC).isoformat()
        _write_json_atomic(manifest_path, manifest)
        if not duration_passed:
            raise VoiceEvaluationError(
                f"generated audio for {case.id!r} is {duration:.3f} seconds; "
                "expected between "
                f"{case.duration_seconds.minimum:.3f} and "
                f"{case.duration_seconds.maximum:.3f} seconds"
            )
    return manifest


def record_voice_review(
    batch_directory: str | Path,
    case_id: str,
    *,
    verdict: Literal["approved", "rejected"],
    reviewer: str,
    notes: str,
) -> dict[str, Any]:
    if verdict not in {"approved", "rejected"}:
        raise VoiceEvaluationError("voice review must be approved or rejected")
    if not reviewer.strip():
        raise VoiceEvaluationError("voice review requires a reviewer")
    if len(notes.strip()) < 10:
        raise VoiceEvaluationError(
            "voice review requires concrete notes of at least 10 characters"
        )
    path = Path(batch_directory) / "manifest.json"
    manifest = _read_manifest(path)
    matches = [item for item in manifest["items"] if item.get("id") == case_id]
    if len(matches) != 1:
        raise VoiceEvaluationError(
            f"voice batch must contain exactly one case named {case_id!r}"
        )
    review: dict[str, Any] = {
        "status": verdict,
        "reviewer": reviewer.strip(),
        "notes": notes.strip(),
        "reviewedAtUtc": datetime.now(UTC).isoformat(),
    }
    matches[0]["voiceReview"] = review
    manifest["updatedAtUtc"] = datetime.now(UTC).isoformat()
    _write_json_atomic(path, manifest)
    return review


def inspect_wav(audio: bytes) -> dict[str, int | float]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as reader:
            frame_count = reader.getnframes()
            sample_rate = reader.getframerate()
            channels = reader.getnchannels()
    except (EOFError, wave.Error) as exc:
        raise VoiceEvaluationError("speech provider output is not valid WAV") from exc
    if frame_count <= 0 or sample_rate <= 0 or channels not in {1, 2}:
        raise VoiceEvaluationError("speech provider returned an empty or invalid WAV")
    return {
        "durationSeconds": round(frame_count / sample_rate, 6),
        "sampleRateHz": sample_rate,
        "channels": channels,
    }


def _load_or_initialize_manifest(
    path: Path,
    *,
    suite: VoiceMotionEvalSuite,
    suite_sha256: str,
    model: str,
    voice: str,
) -> dict[str, Any]:
    if path.exists():
        manifest = _read_manifest(path)
        expected = {
            "suiteId": suite.suite_id,
            "suiteSha256": suite_sha256,
            "voiceStyleVersion": suite.voice_style_version,
            "model": model,
            "voice": voice,
            "audioFormat": "wav",
        }
        mismatches = [
            key for key, value in expected.items() if manifest.get(key) != value
        ]
        if mismatches:
            raise VoiceEvaluationError(
                "existing voice batch has incompatible fields: " + ", ".join(mismatches)
            )
        return manifest
    return {
        "schemaVersion": 1,
        "suiteId": suite.suite_id,
        "suiteSha256": suite_sha256,
        "voiceStyleVersion": suite.voice_style_version,
        "provider": "openai",
        "model": model,
        "voice": voice,
        "audioFormat": "wav",
        "timingAvailability": "audio_sample_clock_only",
        "humanReviewCriteria": suite.human_review_criteria,
        "items": [],
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VoiceEvaluationError(f"voice batch does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise VoiceEvaluationError(
            f"voice batch manifest is invalid JSON: {exc}"
        ) from exc
    if manifest.get("schemaVersion") != 1:
        raise VoiceEvaluationError("unsupported voice batch schemaVersion")
    items = manifest.get("items")
    if not isinstance(items, list):
        raise VoiceEvaluationError("voice batch manifest has no items list")
    return manifest


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
