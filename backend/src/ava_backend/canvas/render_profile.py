from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..realtime.common import RealtimeProtocolModel


class CanvasOrientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"


class CanvasLayoutClass(StrEnum):
    COMPACT = "compact"
    REGULAR = "regular"


class CanvasTouchEnvironment(StrEnum):
    DIRECT = "direct"
    NONE = "none"


class CanvasNormalizedInsets(RealtimeProtocolModel):
    """Safe drawable insets expressed as fractions of the usable canvas."""

    top: float = Field(default=0.0, ge=0.0, le=0.4, allow_inf_nan=False)
    right: float = Field(default=0.0, ge=0.0, le=0.4, allow_inf_nan=False)
    bottom: float = Field(default=0.0, ge=0.0, le=0.4, allow_inf_nan=False)
    left: float = Field(default=0.0, ge=0.0, le=0.4, allow_inf_nan=False)

    @model_validator(mode="after")
    def leaves_a_drawable_region(self) -> CanvasNormalizedInsets:
        if self.left + self.right >= 0.8 or self.top + self.bottom >= 0.8:
            raise ValueError("canvas safe insets leave too little drawable area")
        return self


class CanvasRenderProfile(RealtimeProtocolModel):
    """Client-measured semantic composition context, independent of SVG units."""

    usable_aspect_ratio: float = Field(
        ge=0.25,
        le=4.0,
        allow_inf_nan=False,
        description="Usable canvas width divided by usable canvas height.",
    )
    orientation: CanvasOrientation
    width_class: CanvasLayoutClass
    height_class: CanvasLayoutClass
    safe_insets: CanvasNormalizedInsets = Field(default_factory=CanvasNormalizedInsets)
    touch_environment: CanvasTouchEnvironment = CanvasTouchEnvironment.DIRECT
    minimum_interaction_target_points: float | None = Field(
        default=44.0,
        ge=24.0,
        le=96.0,
        allow_inf_nan=False,
    )
    text_scale_factor: float = Field(
        default=1.0,
        ge=0.75,
        le=3.0,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def orientation_matches_aspect_ratio(self) -> CanvasRenderProfile:
        ratio = self.usable_aspect_ratio
        if self.orientation == CanvasOrientation.PORTRAIT and ratio >= 1.05:
            raise ValueError("portrait canvas render profile must be taller than wide")
        if self.orientation == CanvasOrientation.LANDSCAPE and ratio <= 0.95:
            raise ValueError("landscape canvas render profile must be wider than tall")
        if self.orientation == CanvasOrientation.SQUARE and not 0.95 < ratio < 1.05:
            raise ValueError(
                "square canvas render profile requires a near-square ratio"
            )
        if self.touch_environment == CanvasTouchEnvironment.DIRECT and (
            self.minimum_interaction_target_points is None
        ):
            raise ValueError("direct touch requires a minimum interaction target")
        if self.touch_environment == CanvasTouchEnvironment.NONE and (
            self.minimum_interaction_target_points is not None
        ):
            raise ValueError(
                "minimum interaction target is meaningful only for direct touch"
            )
        return self
