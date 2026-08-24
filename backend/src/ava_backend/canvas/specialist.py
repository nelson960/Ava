from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Annotated, Protocol

from pydantic import Field, StringConstraints, model_validator

from ..protocol import AgeBand
from ..realtime.common import NonEmptyText, ProtocolIdentifier, RealtimeProtocolModel
from ..realtime.model_plan import ModelCanvasDraft
from ..realtime.tool_contract import (
    INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS,
    INTERACTIVE_CANVAS_GOAL_MAX_CHARACTERS,
)

CANVAS_SPECIALIST_PROMPT_VERSION = "ava-canvas-v2.0"
CANVAS_REFERENCE_DESIGN_VERSION = "ava-interactive-reference-v1"
CANVAS_SPECIALIST_BRIEF_MAX_CHARACTERS = INTERACTIVE_CANVAS_BRIEF_MAX_CHARACTERS


class CanvasSpecialistError(RuntimeError):
    """Base error for the optional strict canvas generation boundary."""

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


class CanvasSpecialistUnavailableError(CanvasSpecialistError):
    """The specialist provider could not complete a bounded request."""


class CanvasSpecialistInvalidError(CanvasSpecialistError):
    """The specialist returned no usable strict canvas draft."""


class CanvasSpecialistRequest(RealtimeProtocolModel):
    turn_id: ProtocolIdentifier
    brief: Annotated[
        NonEmptyText,
        StringConstraints(max_length=CANVAS_SPECIALIST_BRIEF_MAX_CHARACTERS),
    ]
    interaction_goal: (
        Annotated[
            NonEmptyText,
            StringConstraints(max_length=INTERACTIVE_CANVAS_GOAL_MAX_CHARACTERS),
        ]
        | None
    ) = None
    age_band: AgeBand
    current_revision: int | None = Field(default=None, ge=1, le=10_001)
    current_scene: ModelCanvasDraft | None = None

    @model_validator(mode="after")
    def current_scene_and_revision_are_atomic(self) -> CanvasSpecialistRequest:
        if (self.current_scene is None) != (self.current_revision is None):
            raise ValueError(
                "currentScene and currentRevision must be supplied together"
            )
        return self

    def model_input(self) -> str:
        current = ""
        if self.current_scene is not None:
            try:
                from ..realtime.model_plan import compile_model_canvas_draft
                from .svg_export import export_transaction_to_svg

                tx = compile_model_canvas_draft(
                    self.current_scene, transaction_id="current_scene"
                )
                serialized = export_transaction_to_svg(tx)
            except Exception:
                serialized = self.current_scene.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
            current = (
                f'<current_scene revision="{self.current_revision}" scene_id="{escape(self.current_scene.scene_id)}" '
                'contract="complete_validated_previous_scene">\n'
                f"{escape(serialized)}\n"
                "</current_scene>\n"
            )
        return (
            f'<canvas_request age_band="{escape(str(self.age_band))}">\n'
            f"<brief>{escape(self.brief)}</brief>\n"
            f"<interaction_goal>{escape(self.interaction_goal or '')}</interaction_goal>\n"
            f"{current}"
            "</canvas_request>"
        )


@dataclass(frozen=True, slots=True)
class CanvasSpecialistGeneration:
    draft: ModelCanvasDraft
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")


class CanvasSpecialist(Protocol):
    async def generate(
        self,
        request: CanvasSpecialistRequest,
    ) -> CanvasSpecialistGeneration: ...

    async def cancel(self, *, turn_id: str) -> None: ...

    async def close(self) -> None: ...


def render_canvas_specialist_instructions() -> str:
    return """# Ava Vector Illustration Studio

You are Ava's master vector illustrator and visual designer.

Your goal is to create a stunning, publication-quality editorial vector illustration that visually explains the concept with clarity, depth, and beauty for children.

CANVAS & VIEWPORT
- 1000 × 600 logical viewport (`<svg viewBox="0 0 1000 600" xmlns="http://www.w3.org/2000/svg">`).
- Keep core focal elements within the comfortable 40-unit inset (x: 40..960, y: 40..560). Full-bleed background/environment spans (0..1000, 0..600) are encouraged.
- NO Bounding Box / Border: Do NOT draw a framing rectangle, card outline, or stroke border around the entire canvas (no `<rect width="1000" height="600" stroke="...">`). The artwork must feel open, organic, and borderless.

VISUAL STYLE & ART DIRECTION
- Aesthetic: Modern editorial vector illustration (clean, sophisticated flat design with depth, reminiscent of high-end science publications and modern educational animations).
- Color Palette: Cohesive, harmonious palette with soft pastels, rich accents, and warm ambient lighting. Avoid harsh neon primaries.
- Depth & Shading: Use smooth `<linearGradient>` and `<radialGradient>` for volume, glow, light falloff, and core highlights. Use `<feDropShadow>` in `<filter id="shadow">` for subtle depth separation.
- Organic Geometry: Prefer fluid, multi-point cubic Bézier curves (`<path d="M... C... Q... Z">`) for natural anatomy, geological cutaways, flowing liquids, and organic forms over rigid geometric primitives.
- Atmosphere & Composition: Build depth with background atmospheric tones, multi-layer environmental strata, contextual scene elements, and clean typography badges.

INTERACTIVITY & HOTSPOTS
- Tag primary educational components with clean, descriptive `id` attributes on `<g>` or `<path>` elements (e.g. `id="magma_chamber"`, `id="conduit"`, `id="crater"`).
- For composite badges or cards (e.g. Family Tree roles or diagram nodes), wrap the card shape, icon, and labels inside a single `<g id="...">` so the whole badge acts as a unified interactive hotspot.
- In `entities`, provide 2 to 6 matching educational hotspots:
  - `id`: Matches the corresponding SVG element `id`
  - `name`: Clean, kid-friendly display title
  - `description`: 1-2 vivid, intuitive sentences explaining how it works
  - `facts`: 2-3 fascinating bullet points

OUTPUT FORMAT
- Return standard, valid SVG in the `svg` field.
- Aim for 25 to 45 well-crafted, expressive vector nodes. This keeps generation swift and rendering crisp.
- No `<script>`, `<foreignObject>`, HTML, or external URLs.
- If a previous scene is supplied, revise that scene rather than unnecessarily replacing its visual identity."""
