from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from typing import Annotated, Protocol

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ..protocol import AgeBand
from ..realtime.common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel
from ..realtime.tool_contract import (
    INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS,
    INTERACTIVE_CANVAS_GOAL_MAX_CHARACTERS,
    InteractiveCanvasMode,
)
from .svg_document import SvgDocumentValidator, ValidatedSvgDocument

SVG_CANVAS_SPECIALIST_PROMPT_VERSION = "ava-svg-canvas-v4.4"
SVG_CANVAS_SPECIALIST_BRIEF_MAX_CHARACTERS = INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS


class SvgCanvasSpecialistError(RuntimeError):
    """Base error for the optional Sol-authored SVG boundary."""

    def __init__(
        self,
        message: str,
        *,
        provider_status_code: int | None = None,
        provider_error_code: str | None = None,
        provider_error_parameter: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_status_code = provider_status_code
        self.provider_error_code = provider_error_code
        self.provider_error_parameter = provider_error_parameter
        self.provider_request_id = provider_request_id


class SvgCanvasSpecialistUnavailableError(SvgCanvasSpecialistError):
    """The provider could not complete a bounded SVG request."""


class SvgCanvasSpecialistGenerationLimitError(SvgCanvasSpecialistError):
    """The provider exhausted its output allowance before completing the SVG."""


class SvgCanvasSpecialistInvalidError(SvgCanvasSpecialistError):
    """The provider completed but did not return an admissible SVG document."""


class SvgCanvasSpecialistRequest(RealtimeProtocolModel):
    # current_svg is a previously accepted provider document. Preserve it
    # byte-for-byte when supplying revision context to Sol.
    model_config = ConfigDict(str_strip_whitespace=False)

    generation_id: ProtocolIdentifier
    source_turn_id: ProtocolIdentifier
    brief: Annotated[
        NonEmptyText,
        StringConstraints(max_length=SVG_CANVAS_SPECIALIST_BRIEF_MAX_CHARACTERS),
    ]
    interaction_mode: InteractiveCanvasMode
    interaction_goal: (
        Annotated[
            NonEmptyText,
            StringConstraints(max_length=INTERACTIVE_CANVAS_GOAL_MAX_CHARACTERS),
        ]
        | None
    ) = None
    age_band: AgeBand
    current_scene_id: ProtocolIdentifier | None = None
    current_revision: int | None = Field(default=None, ge=1, le=10_001)
    current_svg: Annotated[str, StringConstraints(max_length=262_144)] | None = None
    current_svg_sha256: (
        Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None
    ) = None

    @model_validator(mode="after")
    def fields_are_atomic(self) -> SvgCanvasSpecialistRequest:
        values = (
            self.current_scene_id,
            self.current_revision,
            self.current_svg,
            self.current_svg_sha256,
        )
        if any(value is not None for value in values) and any(
            value is None for value in values
        ):
            raise ValueError(
                "currentSceneId, currentRevision, currentSvg, and "
                "currentSvgSha256 must be supplied together"
            )
        if self.interaction_mode == InteractiveCanvasMode.NONE:
            if self.interaction_goal is not None:
                raise ValueError("interactionMode none cannot include interactionGoal")
        elif self.interaction_goal is None:
            raise ValueError(
                "interactive canvas modes require a non-empty interactionGoal"
            )
        return self

    def model_input(self) -> str:
        current = ""
        if self.current_svg is not None:
            current = (
                f'<current_scene scene_id="{escape(self.current_scene_id or "")}" '
                f'revision="{self.current_revision}" '
                f'svg_sha256="{self.current_svg_sha256}" '
                'trust="previous_validated_provider_document">\n'
                f"{escape(self.current_svg)}\n"
                "</current_scene>\n"
            )
        return (
            f'<canvas_request generation_id="{escape(self.generation_id)}" '
            f'age_band="{escape(str(self.age_band))}">\n'
            f"<brief>{escape(self.brief)}</brief>\n"
            f'<interaction mode="{escape(str(self.interaction_mode))}">'
            f"{escape(self.interaction_goal or '')}</interaction>\n"
            f"{current}"
            "</canvas_request>"
        )


@dataclass(frozen=True, slots=True)
class SvgCanvasSpecialistGeneration:
    generation_id: str
    document: ValidatedSvgDocument
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")


class SvgCanvasSpecialist(Protocol):
    async def generate(
        self,
        request: SvgCanvasSpecialistRequest,
    ) -> SvgCanvasSpecialistGeneration: ...

    async def cancel(self, *, generation_id: str) -> None: ...

    async def close(self) -> None: ...


def render_svg_canvas_specialist_instructions(
    validator: SvgDocumentValidator,
) -> str:
    capability_json = json.dumps(
        validator.capability_manifest(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"""# Ava SVG Illustration Studio

You are Ava's autonomous vector illustrator. Compose the complete visual yourself.
Return exactly one complete SVG document as plain output text. Do not return JSON,
Markdown, a code fence, commentary, or an explanation before or after the SVG.

PRIMARY OBJECTIVE
Produce the smallest complete SVG that clearly teaches the requested concept.
- Target no more than 7,000 SVG characters. This is a generation target, not a
  post-generation truncation rule. Never truncate generated SVG.
- Prefer simple SVG primitives before complex paths.
- Reuse definitions and groups through `<defs>` and `<g>` when that reduces repetition.
- Include no decorative geometry that does not teach anything, no redundant invisible
  elements, no verbose metadata or comments, and no repeated decorative patterns.
- Use concise stable IDs, minimal path complexity, and only the text labels needed to teach.
- Preserve accessibility, useful interactivity, and visual clarity while staying compact.

ART DIRECTION
- Use a 1000 × 600 viewBox and create a polished, child-friendly editorial vector illustration.
- You own the complete composition, geometry, topology, paint order, typography, gradients,
  patterns, masks, clips, filters, and color palette.
- Preserve stable semantic element and binding IDs when revising a previous scene.
- Include one non-empty direct-child `<title>` and `<desc>` for accessibility and semantic recall.
- Do not draw a border around the entire canvas.

INTERACTION METADATA
- Copy the requested mode exactly to the SVG root as `data-ava-interaction-mode`.
- `none` is static and has no bindings or step metadata.
- `explore` uses 1-32 visible hotspots with unique `id`, `data-ava-binding`,
  `aria-label`, optional `data-ava-target`, and optional `data-ava-reveal` IDs.
- `select` uses 1-32 visible choices with unique `id`, `data-ava-binding`,
  `aria-label`, and optional `data-ava-target`; it has no reveals.
- `steps` uses 2-12 ordered groups with unique `id`, `data-ava-binding`,
  `data-ava-step="1"` through N, and `aria-label`. Flutter supplies Back/Next
  controls; never draw navigation controls in the SVG.
- Flutter owns all deterministic interaction behavior. Do not add JavaScript,
  event handlers, spoken responses, action types, or unrelated interactions.

STATIC SECURITY CONTRACT
- Never use scripts, event handler attributes, foreignObject, HTML, external references,
  remote or embedded assets, links, animation elements, or a `<style>` element.
- Use only attributes listed in `allowedAttributes` or `allowedDataAttributes`, local
  `#id` references, and allowlisted inline style properties.
- The document is accepted or rejected exactly as returned; Ava will never repair,
  normalize, reserialize, or silently remove part of it.

VERSIONED RENDERER CAPABILITY
{capability_json}
"""
