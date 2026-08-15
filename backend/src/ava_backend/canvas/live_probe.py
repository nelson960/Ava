from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from uuid import uuid4

from ..config import BackendConfigurationError, Settings
from ..protocol import AgeBand
from ..providers.openai_canvas_provider import OpenAICanvasSpecialist
from ..realtime.model_plan import compile_model_canvas_draft
from .specialist import CanvasSpecialistError, CanvasSpecialistRequest
from .validation import CanvasSceneValidator


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run exactly one explicitly authorized strict OpenAI Responses "
            "canvas probe and validate the compiled Ava transaction."
        )
    )
    parser.add_argument("--brief", required=True)
    parser.add_argument(
        "--age-band",
        default=AgeBand.MIDDLE_CHILDHOOD.value,
        choices=tuple(band.value for band in AgeBand),
    )
    parser.add_argument("--max-output-tokens", type=int, required=True)
    parser.add_argument(
        "--allow-spend",
        action="store_true",
        help="Required acknowledgement that this command makes one paid request.",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(_run(arguments)))


async def _run(arguments: argparse.Namespace) -> int:
    settings = Settings()
    if not arguments.allow_spend:
        raise BackendConfigurationError(
            "Live canvas probe refused: pass --allow-spend explicitly"
        )
    if not settings.openai_live_tests_enabled:
        raise BackendConfigurationError(
            "Live canvas probe refused: set "
            "AVA_OPENAI_LIVE_TESTS_ENABLED=true for this command"
        )
    if (
        not 512
        <= arguments.max_output_tokens
        <= settings.openai_canvas_max_output_tokens
    ):
        raise BackendConfigurationError(
            "--max-output-tokens must be between 512 and "
            "AVA_OPENAI_CANVAS_MAX_OUTPUT_TOKENS"
        )

    request_nonce = uuid4().hex
    provider = OpenAICanvasSpecialist(
        settings.model_copy(
            update={"openai_canvas_max_output_tokens": arguments.max_output_tokens}
        ),
        safety_identifier=hashlib.sha256(b"ava-live-canvas-probe-v1").hexdigest(),
    )
    request = CanvasSpecialistRequest.model_validate(
        {
            "turnId": f"turn-live-canvas-{request_nonce}",
            "brief": arguments.brief,
            "ageBand": arguments.age_band,
        }
    )
    try:
        generation = await provider.generate(request)
        transaction = compile_model_canvas_draft(
            generation.draft,
            transaction_id=f"canvas_live_{request_nonce[:16]}",
        )
        CanvasSceneValidator().apply(transaction)
    except CanvasSpecialistError as error:
        print(
            json.dumps(
                {
                    "status": "rejected",
                    "reason": type(error).__name__,
                    "message": str(error),
                    "providerStatusCode": error.provider_status_code,
                    "providerErrorCode": error.provider_error_code,
                    "providerErrorParameter": error.provider_error_parameter,
                    "providerRequestId": error.provider_request_id,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        await provider.close()

    print(
        json.dumps(
            {
                "status": "accepted",
                "model": settings.openai_canvas_model,
                "providerResponseId": generation.provider_request_id,
                "usage": {
                    "inputTokens": generation.input_tokens,
                    "outputTokens": generation.output_tokens,
                    "cachedInputTokens": generation.cached_input_tokens,
                },
                "transaction": transaction.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    main()
