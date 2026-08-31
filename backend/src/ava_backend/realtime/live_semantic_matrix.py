from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, StringConstraints

from ..config import BackendConfigurationError, Settings
from ..protocol import AgeBand
from ..providers.openai_realtime_provider import (
    OpenAIRealtimeLiveProbe,
    OpenAIRealtimeProbeFailureResult,
    OpenAIRealtimeProbeRequest,
)
from .common import NonEmptyText, RealtimeProtocolModel
from .harness import REALTIME_PROMPT_VERSION, build_realtime_harness
from .memory import AuthoritativeMemorySnapshot


class SemanticMatrixCase(RealtimeProtocolModel):
    id: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=80),
    ]
    input: Annotated[NonEmptyText, StringConstraints(max_length=2_000)]
    expect_canvas: bool


class SemanticMatrixSuite(RealtimeProtocolModel):
    schema_version: Literal[1]
    prompt_version: Annotated[NonEmptyText, StringConstraints(max_length=80)]
    cases: list[SemanticMatrixCase] = Field(min_length=1, max_length=8)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run Ava's capped semantic canvas-selection matrix in one paid, "
            "persistent OpenAI Realtime session."
        )
    )
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--max-output-tokens", required=True, type=int)
    parser.add_argument("--allow-spend", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(_run(arguments)))


async def _run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    if not arguments.allow_spend:
        raise BackendConfigurationError(
            "Live semantic matrix refused: pass --allow-spend explicitly"
        )
    if not settings.openai_live_tests_enabled:
        raise BackendConfigurationError(
            "Live semantic matrix refused: set "
            "AVA_OPENAI_LIVE_TESTS_ENABLED=true for this command"
        )
    if not 256 <= arguments.max_output_tokens <= 800:
        raise BackendConfigurationError(
            "--max-output-tokens must be between 256 and 800"
        )
    suite = SemanticMatrixSuite.model_validate_json(
        arguments.suite.read_text(encoding="utf-8")
    )
    if suite.prompt_version != REALTIME_PROMPT_VERSION:
        raise BackendConfigurationError(
            "semantic matrix promptVersion does not match the active prompt"
        )
    harness = build_realtime_harness(
        AuthoritativeMemorySnapshot.model_validate(
            {
                "ageBand": AgeBand.MIDDLE_CHILDHOOD,
                "resetRevision": 0,
                "currentSessionSummary": None,
                "deliveredTurns": [],
                "stableMemories": [],
            }
        )
    )
    probe = OpenAIRealtimeLiveProbe(
        api_key=settings.require_openai_api_key(),
        model=settings.openai_realtime_model,
        safety_identifier=hashlib.sha256(
            b"ava-live-realtime-semantic-matrix-v1"
        ).hexdigest(),
        connect_timeout_seconds=settings.openai_realtime_connect_timeout_seconds,
        turn_timeout_seconds=settings.realtime_max_turn_seconds,
        max_output_tokens=settings.openai_realtime_max_output_tokens,
        truncation_post_instructions_tokens=(
            settings.openai_realtime_truncation_post_instructions_tokens
        ),
        truncation_retention_ratio=(
            settings.openai_realtime_truncation_retention_ratio
        ),
    )
    results = await probe.run_many(
        harness=harness,
        requests=tuple(
            OpenAIRealtimeProbeRequest(
                turn_id=f"turn-live-matrix-{case.id}-{uuid4()}",
                text_input=case.input,
                max_output_tokens=arguments.max_output_tokens,
            )
            for case in suite.cases
        ),
    )
    failures = []
    rows = []
    total_tokens = 0
    for case, result in zip(suite.cases, results, strict=True):
        if isinstance(result, OpenAIRealtimeProbeFailureResult):
            failures.append(case.id)
            total_tokens += result.usage.total_tokens
            rows.append(
                {
                    "id": case.id,
                    "passed": False,
                    "expectedCanvas": case.expect_canvas,
                    "actualCanvas": None,
                    "canvasContractValid": None,
                    "canvasRejection": None,
                    "providerResponseId": result.response_id,
                    "firstDeltaMs": None,
                    "completedMs": None,
                    "usage": asdict(result.usage),
                    "speech": None,
                    "canvasRequest": None,
                    "validationIssues": [
                        {
                            "path": list(issue.path),
                            "code": issue.code,
                        }
                        for issue in result.validation_issues
                    ],
                }
            )
            continue
        actual_canvas = result.canvas_selected
        canvas_contract_valid = result.canvas_rejection is None
        passed = actual_canvas == case.expect_canvas and (
            not actual_canvas or canvas_contract_valid
        )
        if not passed:
            failures.append(case.id)
        total_tokens += result.usage.total_tokens
        rows.append(
            {
                "id": case.id,
                "passed": passed,
                "expectedCanvas": case.expect_canvas,
                "actualCanvas": actual_canvas,
                "canvasContractValid": canvas_contract_valid,
                "canvasRejection": result.canvas_rejection,
                "providerResponseId": result.response_id,
                "firstDeltaMs": round(result.first_delta_ms, 2),
                "completedMs": round(result.completed_ms, 2),
                "usage": asdict(result.usage),
                "speech": result.speech,
                "canvasRequest": (
                    result.canvas_request.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                    if result.canvas_request is not None
                    else None
                ),
            }
        )
    print(
        json.dumps(
            {
                "status": "accepted" if not failures else "rejected",
                "model": settings.openai_realtime_model,
                "promptVersion": suite.prompt_version,
                "persistentSession": True,
                "caseCount": len(rows),
                "failedCaseIds": failures,
                "totalTokens": total_tokens,
                "cases": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    main()
