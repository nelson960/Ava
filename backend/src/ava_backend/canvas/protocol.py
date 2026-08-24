from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ..realtime.common import (
    NodeIdentifier,
    NonEmptyText,
    ProtocolIdentifier,
    RealtimeProtocolModel,
)

CANVAS_MAX_NODES_PER_TURN = 96
CANVAS_MAX_OPERATIONS_PER_TRANSACTION = 128
CANVAS_MAX_BINDINGS_PER_TRANSACTION = 24
CANVAS_MAX_STATE_VARIABLES = 16
CANVAS_MAX_PATH_CHARACTERS = 2_000
CANVAS_MAX_POINTS = 64
CANVAS_MAX_TEXT_CHARACTERS = 240
CANVAS_MAX_TRANSACTIONS_PER_TURN = 8
CANVAS_LOGICAL_WIDTH = 1_000.0
CANVAS_LOGICAL_HEIGHT = 600.0

CanvasHexColor = Annotated[
    str,
    StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$"),
]


class CanvasRendererLimits(RealtimeProtocolModel):
    max_nodes: int = Field(ge=1)
    max_operations_per_transaction: int = Field(ge=1)
    max_bindings_per_transaction: int = Field(ge=0)
    max_state_variables: int = Field(ge=0)
    max_path_characters: int = Field(ge=1)
    max_path_commands: int = Field(ge=1)
    max_path_numbers: int = Field(ge=1)
    max_points_per_node: int = Field(ge=2)
    max_text_characters: int = Field(ge=1)
    max_text_lines: int = Field(ge=1)
    max_hierarchy_depth: int = Field(ge=1)


class CanvasRendererCapabilityManifest(RealtimeProtocolModel):
    grammar_version: int = Field(ge=1)
    logical_width: float = Field(gt=0, allow_inf_nan=False)
    logical_height: float = Field(gt=0, allow_inf_nan=False)
    node_kinds: list[str]
    operations: list[str]
    interactions: list[str]
    animation_properties: list[str]
    color_tokens: list[str]
    limits: CanvasRendererLimits


CANVAS_RENDERER_CAPABILITY_MANIFEST = CanvasRendererCapabilityManifest.model_validate(
    {
        "grammarVersion": 3,
        "logicalWidth": CANVAS_LOGICAL_WIDTH,
        "logicalHeight": CANVAS_LOGICAL_HEIGHT,
        "nodeKinds": [
            "group",
            "text",
            "line",
            "arrow",
            "circle",
            "ellipse",
            "rect",
            "polygon",
            "polyline",
            "path",
        ],
        "operations": [
            "create",
            "update",
            "delete",
            "reveal",
            "focus",
            "animate",
            "clear",
        ],
        "interactions": [
            "explore",
            "toggleVisibility",
            "select",
            "reveal",
            "dragWithin",
            "toggleState",
            "setScalar",
            "setEnum",
            "reset",
            "submitOutcome",
        ],
        "animationProperties": [
            "opacity",
            "translateX",
            "translateY",
            "rotationDegrees",
            "scale",
        ],
        "colorTokens": [
            "ink",
            "muted",
            "accent",
            "accentSoft",
            "positive",
            "warning",
            "paper",
            "transparent",
        ],
        "limits": {
            "maxNodes": CANVAS_MAX_NODES_PER_TURN,
            "maxOperationsPerTransaction": CANVAS_MAX_OPERATIONS_PER_TRANSACTION,
            "maxBindingsPerTransaction": CANVAS_MAX_BINDINGS_PER_TRANSACTION,
            "maxStateVariables": CANVAS_MAX_STATE_VARIABLES,
            "maxPathCharacters": CANVAS_MAX_PATH_CHARACTERS,
            "maxPathCommands": 128,
            "maxPathNumbers": 512,
            "maxPointsPerNode": CANVAS_MAX_POINTS,
            "maxTextCharacters": CANVAS_MAX_TEXT_CHARACTERS,
            "maxTextLines": 4,
            "maxHierarchyDepth": 8,
        },
    }
)


class CanvasColorToken(StrEnum):
    INK = "ink"
    MUTED = "muted"
    ACCENT = "accent"
    ACCENT_SOFT = "accentSoft"
    POSITIVE = "positive"
    WARNING = "warning"
    PAPER = "paper"
    TRANSPARENT = "transparent"


class CanvasStrokeCap(StrEnum):
    BUTT = "butt"
    ROUND = "round"
    SQUARE = "square"


class CanvasFontWeight(StrEnum):
    REGULAR = "regular"
    MEDIUM = "medium"
    BOLD = "bold"


class CanvasTextAnchor(StrEnum):
    START = "start"
    MIDDLE = "middle"
    END = "end"


class CanvasEasing(StrEnum):
    LINEAR = "linear"
    EASE_IN_OUT = "easeInOut"
    EASE_OUT = "easeOut"


class CanvasAnimationProperty(StrEnum):
    OPACITY = "opacity"
    TRANSLATE_X = "translateX"
    TRANSLATE_Y = "translateY"
    ROTATION_DEGREES = "rotationDegrees"
    SCALE = "scale"


class CanvasTransform(RealtimeProtocolModel):
    translate_x: float = Field(ge=-2_000, le=2_000, allow_inf_nan=False)
    translate_y: float = Field(ge=-1_200, le=1_200, allow_inf_nan=False)
    scale: float = Field(ge=0.05, le=8, allow_inf_nan=False)
    rotation_degrees: float = Field(
        ge=-360,
        le=360,
        allow_inf_nan=False,
    )
    pivot_x: float = Field(
        default=0,
        ge=-2_000,
        le=3_000,
        allow_inf_nan=False,
        exclude_if=lambda value: value == 0,
    )
    pivot_y: float = Field(
        default=0,
        ge=-1_200,
        le=1_800,
        allow_inf_nan=False,
        exclude_if=lambda value: value == 0,
    )


class CanvasPoint(RealtimeProtocolModel):
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)


class CanvasGradientStop(RealtimeProtocolModel):
    offset: float = Field(ge=0, le=1, allow_inf_nan=False)
    color: CanvasHexColor


class CanvasLinearGradient(RealtimeProtocolModel):
    start: CanvasPoint
    end: CanvasPoint
    stops: list[CanvasGradientStop] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def stops_are_strictly_ordered(self) -> CanvasLinearGradient:
        offsets = [stop.offset for stop in self.stops]
        if offsets != sorted(offsets) or len(offsets) != len(set(offsets)):
            raise ValueError("gradient stops must be strictly ordered")
        if offsets[0] != 0 or offsets[-1] != 1:
            raise ValueError("gradient stops must begin at 0 and end at 1")
        return self


class CanvasRadialGradient(RealtimeProtocolModel):
    center: CanvasPoint
    radius: float = Field(gt=0, le=2_000, allow_inf_nan=False)
    stops: list[CanvasGradientStop] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def stops_are_strictly_ordered(self) -> CanvasRadialGradient:
        offsets = [stop.offset for stop in self.stops]
        if offsets != sorted(offsets) or len(offsets) != len(set(offsets)):
            raise ValueError("radial gradient stops must be strictly ordered")
        if offsets[0] != 0 or offsets[-1] != 1:
            raise ValueError("radial gradient stops must begin at 0 and end at 1")
        return self


class CanvasShadow(RealtimeProtocolModel):
    color: CanvasHexColor
    opacity: float = Field(ge=0, le=0.5, allow_inf_nan=False)
    blur_radius: float = Field(ge=0, le=32, allow_inf_nan=False)
    offset_x: float = Field(ge=-24, le=24, allow_inf_nan=False)
    offset_y: float = Field(ge=-24, le=24, allow_inf_nan=False)


class CanvasStyle(RealtimeProtocolModel):
    fill: CanvasColorToken
    stroke: CanvasColorToken
    stroke_width: float = Field(ge=0, le=16, allow_inf_nan=False)
    opacity: float = Field(ge=0, le=1, allow_inf_nan=False)
    stroke_cap: CanvasStrokeCap
    fill_color: CanvasHexColor | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    stroke_color: CanvasHexColor | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    gradient: CanvasLinearGradient | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    radial_gradient: CanvasRadialGradient | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    shadow: CanvasShadow | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def custom_fill_is_unambiguous(self) -> CanvasStyle:
        fills = sum(
            value is not None
            for value in (self.fill_color, self.gradient, self.radial_gradient)
        )
        if fills > 1:
            raise ValueError(
                "canvas style must choose one of fillColor, gradient, or radialGradient"
            )
        return self


class CanvasNodeBase(RealtimeProtocolModel):
    id: NodeIdentifier
    parent_id: NodeIdentifier | None
    label: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    visible: bool
    transform: CanvasTransform
    style: CanvasStyle


class CanvasGroupNode(CanvasNodeBase):
    kind: Literal["group"]


class CanvasTextNode(CanvasNodeBase):
    kind: Literal["text"]
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    text: Annotated[
        NonEmptyText,
        StringConstraints(max_length=CANVAS_MAX_TEXT_CHARACTERS),
    ]
    font_size: float = Field(ge=8, le=96, allow_inf_nan=False)
    font_weight: CanvasFontWeight
    text_anchor: CanvasTextAnchor

    @model_validator(mode="after")
    def text_uses_readable_foreground(self) -> CanvasTextNode:
        if self.style.fill not in {
            CanvasColorToken.INK,
            CanvasColorToken.MUTED,
            CanvasColorToken.POSITIVE,
        }:
            raise ValueError(
                "text fill must use an approved high-contrast foreground token"
            )
        return self


class CanvasLineNode(CanvasNodeBase):
    kind: Literal["line", "arrow"]
    start: CanvasPoint
    end: CanvasPoint


class CanvasCircleNode(CanvasNodeBase):
    kind: Literal["circle"]
    center: CanvasPoint
    radius: float = Field(gt=0, le=2_000, allow_inf_nan=False)


class CanvasEllipseNode(CanvasNodeBase):
    kind: Literal["ellipse"]
    center: CanvasPoint
    radius_x: float = Field(gt=0, le=2_000, allow_inf_nan=False)
    radius_y: float = Field(gt=0, le=1_200, allow_inf_nan=False)


class CanvasRectNode(CanvasNodeBase):
    kind: Literal["rect"]
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    width: float = Field(gt=0, le=3_000, allow_inf_nan=False)
    height: float = Field(gt=0, le=1_800, allow_inf_nan=False)
    corner_radius: float = Field(ge=0, le=300, allow_inf_nan=False)


class CanvasPointsNode(CanvasNodeBase):
    kind: Literal["polygon", "polyline"]
    points: list[CanvasPoint] = Field(min_length=2, max_length=CANVAS_MAX_POINTS)

    @model_validator(mode="after")
    def polygon_has_enough_points(self) -> CanvasPointsNode:
        if self.kind == "polygon" and len(self.points) < 3:
            raise ValueError("polygon nodes require at least three points")
        return self


_PATH_ALLOWED = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]+$")


class CanvasPathNode(CanvasNodeBase):
    kind: Literal["path"]
    path_data: Annotated[
        NonEmptyText,
        StringConstraints(max_length=CANVAS_MAX_PATH_CHARACTERS),
    ]

    @model_validator(mode="after")
    def path_uses_only_vector_tokens(self) -> CanvasPathNode:
        if _PATH_ALLOWED.fullmatch(self.path_data) is None:
            raise ValueError("pathData contains unsupported characters")
        command_count = sum(character.isalpha() for character in self.path_data)
        if command_count == 0 or command_count > 128:
            raise ValueError("pathData requires between 1 and 128 commands")
        return self


CanvasNode = Annotated[
    CanvasGroupNode
    | CanvasTextNode
    | CanvasLineNode
    | CanvasCircleNode
    | CanvasEllipseNode
    | CanvasRectNode
    | CanvasPointsNode
    | CanvasPathNode,
    Field(discriminator="kind"),
]


class CanvasCreateOperation(RealtimeProtocolModel):
    operation: Literal["create"]
    node: CanvasNode


class CanvasUpdateOperation(RealtimeProtocolModel):
    operation: Literal["update"]
    node: CanvasNode


class CanvasDeleteOperation(RealtimeProtocolModel):
    operation: Literal["delete"]
    node_id: NodeIdentifier


class CanvasRevealOperation(RealtimeProtocolModel):
    operation: Literal["reveal"]
    node_id: NodeIdentifier


class CanvasFocusOperation(RealtimeProtocolModel):
    operation: Literal["focus"]
    node_id: NodeIdentifier


class CanvasAnimateOperation(RealtimeProtocolModel):
    operation: Literal["animate"]
    node_id: NodeIdentifier
    property: CanvasAnimationProperty
    from_value: float = Field(allow_inf_nan=False)
    to_value: float = Field(allow_inf_nan=False)
    duration_ms: int = Field(ge=80, le=20_000)
    delay_ms: int = Field(ge=0, le=2_500)
    easing: CanvasEasing
    repeat: bool = Field(default=False, exclude_if=lambda value: not value)
    autoreverse: bool = Field(default=False, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def repeated_animation_is_bounded(self) -> CanvasAnimateOperation:
        if self.autoreverse and not self.repeat:
            raise ValueError("autoreverse requires repeat")
        if self.repeat and self.duration_ms < 600:
            raise ValueError("repeating animations must last at least 600ms")
        return self

    @model_validator(mode="after")
    def animation_values_are_safe(self) -> CanvasAnimateOperation:
        limits = {
            CanvasAnimationProperty.OPACITY: (0.0, 1.0),
            CanvasAnimationProperty.TRANSLATE_X: (-2_000.0, 2_000.0),
            CanvasAnimationProperty.TRANSLATE_Y: (-1_200.0, 1_200.0),
            CanvasAnimationProperty.ROTATION_DEGREES: (-360.0, 360.0),
            CanvasAnimationProperty.SCALE: (0.05, 8.0),
        }
        lower, upper = limits[CanvasAnimationProperty(self.property)]
        if not (
            math.isfinite(self.from_value)
            and math.isfinite(self.to_value)
            and lower <= self.from_value <= upper
            and lower <= self.to_value <= upper
        ):
            raise ValueError(
                f"{self.property} animation values must be within {lower}..{upper}"
            )
        return self


class CanvasClearOperation(RealtimeProtocolModel):
    operation: Literal["clear"]


CanvasOperation = Annotated[
    CanvasCreateOperation
    | CanvasUpdateOperation
    | CanvasDeleteOperation
    | CanvasRevealOperation
    | CanvasFocusOperation
    | CanvasAnimateOperation
    | CanvasClearOperation,
    Field(discriminator="operation"),
]


class CanvasBounds(RealtimeProtocolModel):
    left: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    top: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    right: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    bottom: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds_have_area(self) -> CanvasBounds:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("canvas bounds must have positive width and height")
        return self


class CanvasToggleVisibilityAction(RealtimeProtocolModel):
    kind: Literal["toggleVisibility"]
    target_node_id: NodeIdentifier


class CanvasSelectAction(RealtimeProtocolModel):
    kind: Literal["select"]
    target_node_id: NodeIdentifier


class CanvasDragWithinAction(RealtimeProtocolModel):
    kind: Literal["dragWithin"]
    target_node_id: NodeIdentifier
    bounds: CanvasBounds


class CanvasResetAction(RealtimeProtocolModel):
    kind: Literal["reset"]


class CanvasSubmitOutcomeAction(RealtimeProtocolModel):
    kind: Literal["submitOutcome"]
    outcome_id: NodeIdentifier
    value: Annotated[NonEmptyText, StringConstraints(max_length=80)]


class CanvasRevealAction(RealtimeProtocolModel):
    kind: Literal["reveal"]
    target_node_id: NodeIdentifier


class CanvasExploreAction(RealtimeProtocolModel):
    """One bounded, local multi-stage exploration response.

    The renderer owns the behavior. The provider may only name validated scene
    nodes to select, reveal, and briefly animate; it cannot supply executable
    actions or timing code.
    """

    kind: Literal["explore"]
    target_node_id: NodeIdentifier
    reveal_node_ids: list[NodeIdentifier] = Field(min_length=1, max_length=4)
    animate_node_ids: list[NodeIdentifier] = Field(max_length=4)
    exclusive: bool

    @model_validator(mode="after")
    def references_are_unique(self) -> CanvasExploreAction:
        if len(self.reveal_node_ids) != len(set(self.reveal_node_ids)):
            raise ValueError("explore revealNodeIds must be unique")
        if len(self.animate_node_ids) != len(set(self.animate_node_ids)):
            raise ValueError("explore animateNodeIds must be unique")
        if self.target_node_id in self.reveal_node_ids:
            raise ValueError("explore target must remain visible")
        return self


class CanvasToggleStateAction(RealtimeProtocolModel):
    kind: Literal["toggleState"]
    target_state_id: NodeIdentifier


class CanvasSetScalarAction(RealtimeProtocolModel):
    kind: Literal["setScalar"]
    target_state_id: NodeIdentifier
    value: float = Field(allow_inf_nan=False)


class CanvasSetEnumAction(RealtimeProtocolModel):
    kind: Literal["setEnum"]
    target_state_id: NodeIdentifier
    value: Annotated[NonEmptyText, StringConstraints(max_length=40)]


CanvasInteractionAction = Annotated[
    CanvasExploreAction
    | CanvasToggleVisibilityAction
    | CanvasSelectAction
    | CanvasDragWithinAction
    | CanvasResetAction
    | CanvasSubmitOutcomeAction
    | CanvasRevealAction
    | CanvasToggleStateAction
    | CanvasSetScalarAction
    | CanvasSetEnumAction,
    Field(discriminator="kind"),
]


class CanvasInteractionEvent(StrEnum):
    TAP = "tap"
    DRAG = "drag"


class CanvasInteractionBinding(RealtimeProtocolModel):
    id: NodeIdentifier
    node_id: NodeIdentifier
    event: CanvasInteractionEvent
    action: CanvasInteractionAction

    @model_validator(mode="after")
    def event_matches_action(self) -> CanvasInteractionBinding:
        is_drag = self.event == CanvasInteractionEvent.DRAG
        uses_drag_action = self.action.kind == "dragWithin"
        if is_drag != uses_drag_action:
            raise ValueError("drag events and dragWithin actions must occur together")
        return self


class CanvasBooleanStateVariable(RealtimeProtocolModel):
    id: NodeIdentifier
    type: Literal["boolean"]
    initial: bool


class CanvasScalarStateVariable(RealtimeProtocolModel):
    id: NodeIdentifier
    type: Literal["scalar"]
    minimum: float = Field(allow_inf_nan=False)
    maximum: float = Field(allow_inf_nan=False)
    step: float = Field(gt=0, allow_inf_nan=False)
    initial: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def scalar_range_is_coherent(self) -> CanvasScalarStateVariable:
        if self.minimum >= self.maximum:
            raise ValueError("scalar state minimum must be below maximum")
        if not self.minimum <= self.initial <= self.maximum:
            raise ValueError("scalar state initial must be inside its range")
        span = self.maximum - self.minimum
        if self.step > span:
            raise ValueError("scalar state step cannot exceed its range")
        steps = (self.initial - self.minimum) / self.step
        if not math.isclose(steps, round(steps), abs_tol=1e-8):
            raise ValueError("scalar state initial must align to its step")
        return self


class CanvasEnumStateVariable(RealtimeProtocolModel):
    id: NodeIdentifier
    type: Literal["enum"]
    values: list[Annotated[NonEmptyText, StringConstraints(max_length=40)]] = Field(
        min_length=2,
        max_length=16,
    )
    initial: Annotated[NonEmptyText, StringConstraints(max_length=40)]

    @model_validator(mode="after")
    def enum_values_are_coherent(self) -> CanvasEnumStateVariable:
        if len(self.values) != len(set(self.values)):
            raise ValueError("enum state values must be unique")
        if self.initial not in self.values:
            raise ValueError("enum state initial must be one of its values")
        return self


CanvasStateVariable = Annotated[
    CanvasBooleanStateVariable | CanvasScalarStateVariable | CanvasEnumStateVariable,
    Field(discriminator="type"),
]


class AvaCanvasTransaction(RealtimeProtocolModel):
    id: ProtocolIdentifier
    scene_id: ProtocolIdentifier
    base_revision: int = Field(ge=0, le=10_000)
    revision: int = Field(ge=1, le=10_001)
    operations: list[CanvasOperation] = Field(
        min_length=1,
        max_length=CANVAS_MAX_OPERATIONS_PER_TRANSACTION,
        description=(
            "Atomic scene changes. One transaction may create every visual node "
            "needed for the scene; do not split nodes into separate cues."
        ),
    )
    state_variables: list[CanvasStateVariable] = Field(
        max_length=CANVAS_MAX_STATE_VARIABLES
    )
    bindings: list[CanvasInteractionBinding] = Field(
        max_length=CANVAS_MAX_BINDINGS_PER_TRANSACTION
    )

    @model_validator(mode="after")
    def transaction_is_coherent(self) -> AvaCanvasTransaction:
        if self.revision != self.base_revision + 1:
            raise ValueError("canvas revision must equal baseRevision + 1")

        binding_ids = [binding.id for binding in self.bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("canvas binding ids must be unique per transaction")

        state_ids = [variable.id for variable in self.state_variables]
        if len(state_ids) != len(set(state_ids)):
            raise ValueError("canvas state ids must be unique per transaction")

        created_ids = [
            operation.node.id
            for operation in self.operations
            if isinstance(operation, CanvasCreateOperation)
        ]
        if len(created_ids) != len(set(created_ids)):
            raise ValueError("canvas create operations must use unique node ids")

        clear_count = sum(
            isinstance(operation, CanvasClearOperation) for operation in self.operations
        )
        if clear_count and (clear_count != 1 or len(self.operations) != 1):
            raise ValueError("clear must be the only operation in a transaction")
        return self
