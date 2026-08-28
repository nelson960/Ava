from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from ..canvas.protocol import (
    CANVAS_MAX_POINTS,
    AvaCanvasTransaction,
    CanvasCircleNode,
    CanvasAnimateOperation,
    CanvasAnimationProperty,
    CanvasBounds,
    CanvasColorToken,
    CanvasCreateOperation,
    CanvasEllipseNode,
    CanvasExploreAction,
    CanvasDragWithinAction,
    CanvasGroupNode,
    CanvasInteractionBinding,
    CanvasLinearGradient,
    CanvasRadialGradient,
    CanvasLineNode,
    CanvasPathNode,
    CanvasPoint,
    CanvasPointsNode,
    CanvasRectNode,
    CanvasRevealAction,
    CanvasResetAction,
    CanvasShadow,
    CanvasSelectAction,
    CanvasTextNode,
    CanvasToggleVisibilityAction,
    CanvasEasing,
)
from .common import NodeIdentifier, NonEmptyText, RealtimeProtocolModel
from .plan import AvaCanvasCue, AvaPhraseAnchor, SegmentIdentifier


class ModelCanvasNodeKind(StrEnum):
    GROUP = "group"
    TEXT = "text"
    LINE = "line"
    ARROW = "arrow"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    RECT = "rect"
    POLYGON = "polygon"
    POLYLINE = "polyline"
    PATH = "path"


class ModelCanvasStyleRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    OUTLINE = "outline"
    LABEL = "label"
    POSITIVE = "positive"
    WARNING = "warning"
    MUTED = "muted"


class ModelCanvasDesignRole(StrEnum):
    TITLE = "title"
    PRIMARY_LABEL = "primaryLabel"
    SECONDARY_LABEL = "secondaryLabel"
    EXPLANATORY_NOTE = "explanatoryNote"
    PRIMARY_OBJECT = "primaryObject"
    SECONDARY_OBJECT = "secondaryObject"
    CONNECTOR = "connector"
    INTERACTIVE_CONTROL = "interactiveControl"


class ModelCanvasInteractionAction(StrEnum):
    REVEAL = "reveal"
    TOGGLE_VISIBILITY = "toggleVisibility"
    SELECT = "select"
    DRAG_WITHIN = "dragWithin"
    RESET = "reset"
    EXPLORE = "explore"


class ModelCanvasVisualType(StrEnum):
    SYSTEM = "system"
    PROCESS = "process"
    COMPARISON = "comparison"
    SEQUENCE = "sequence"
    MAP = "map"
    ANATOMY = "anatomy"
    QUANTITY = "quantity"
    FREEFORM = "freeform"


class ModelCanvasTopology(StrEnum):
    RADIAL = "radial"
    CYCLIC = "cyclic"
    LINEAR = "linear"
    HIERARCHICAL = "hierarchical"
    SPATIAL = "spatial"
    SIDE_BY_SIDE = "sideBySide"
    COORDINATE = "coordinate"
    LAYERED = "layered"


class ModelCanvasVisualStyle(RealtimeProtocolModel):
    """Bounded authored styling that remains data, never executable markup."""

    fill_color: (
        Annotated[
            str,
            StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$"),
        ]
        | None
    ) = None
    stroke_color: (
        Annotated[
            str,
            StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$"),
        ]
        | None
    ) = None
    stroke_width: float | None = Field(default=None, ge=0, le=16)
    opacity: float | None = Field(default=None, ge=0, le=1)
    gradient: CanvasLinearGradient | None = None
    radial_gradient: CanvasRadialGradient | None = None
    shadow: CanvasShadow | None = None

    @model_validator(mode="after")
    def fill_is_unambiguous(self) -> ModelCanvasVisualStyle:
        if self.gradient is not None or self.radial_gradient is not None:
            if self.fill_color is not None:
                object.__setattr__(self, "fill_color", None)
            if self.gradient is not None and self.radial_gradient is not None:
                object.__setattr__(self, "radial_gradient", None)
        return self


class _ModelCanvasNodeBase(RealtimeProtocolModel):
    id: NodeIdentifier
    parent_id: NodeIdentifier | None
    label: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    style: ModelCanvasStyleRole
    visual_style: ModelCanvasVisualStyle | None = None
    hidden: bool
    pivot: CanvasPoint | None = None
    x: float | None = Field(default=None, ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=-1_200, le=1_800, allow_inf_nan=False)


class ModelCanvasGroupNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.GROUP]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT


class ModelCanvasTextNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.TEXT]
    design_role: Literal[
        ModelCanvasDesignRole.TITLE,
        ModelCanvasDesignRole.PRIMARY_LABEL,
        ModelCanvasDesignRole.SECONDARY_LABEL,
        ModelCanvasDesignRole.EXPLANATORY_NOTE,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_LABEL
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
    ]
    font_size: float | None = Field(default=None, ge=8, le=96, allow_inf_nan=False)
    font_weight: Literal["bold", "medium", "regular"] | None = None
    text_anchor: Literal["start", "middle", "end"] | None = None


class ModelCanvasLineNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.LINE]
    design_role: Literal[ModelCanvasDesignRole.CONNECTOR] = (
        ModelCanvasDesignRole.CONNECTOR
    )
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    x2: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y2: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)


class ModelCanvasArrowNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.ARROW]
    design_role: Literal[ModelCanvasDesignRole.CONNECTOR] = (
        ModelCanvasDesignRole.CONNECTOR
    )
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    x2: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y2: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)


class ModelCanvasCircleNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.CIRCLE]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    radius: float = Field(gt=0, le=2_000, allow_inf_nan=False)


class ModelCanvasEllipseNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.ELLIPSE]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    radius_x: float = Field(gt=0, le=2_000, allow_inf_nan=False)
    radius_y: float = Field(gt=0, le=1_200, allow_inf_nan=False)


class ModelCanvasRectNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.RECT]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT
    x: float = Field(ge=-2_000, le=3_000, allow_inf_nan=False)
    y: float = Field(ge=-1_200, le=1_800, allow_inf_nan=False)
    width: float = Field(gt=0, le=3_000, allow_inf_nan=False)
    height: float = Field(gt=0, le=1_800, allow_inf_nan=False)
    corner_radius: float = Field(default=0, ge=0, le=300, allow_inf_nan=False)


class ModelCanvasPolygonNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.POLYGON]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT
    points: list[CanvasPoint] = Field(
        min_length=3,
        max_length=CANVAS_MAX_POINTS,
    )


class ModelCanvasPolylineNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.POLYLINE]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT
    points: list[CanvasPoint] = Field(
        min_length=2,
        max_length=CANVAS_MAX_POINTS,
    )


class ModelCanvasPathNode(_ModelCanvasNodeBase):
    kind: Literal[ModelCanvasNodeKind.PATH]
    design_role: Literal[
        ModelCanvasDesignRole.PRIMARY_OBJECT,
        ModelCanvasDesignRole.SECONDARY_OBJECT,
        ModelCanvasDesignRole.INTERACTIVE_CONTROL,
    ] = ModelCanvasDesignRole.PRIMARY_OBJECT
    path_data: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]


type ModelCanvasNode = (
    ModelCanvasGroupNode
    | ModelCanvasTextNode
    | ModelCanvasLineNode
    | ModelCanvasArrowNode
    | ModelCanvasCircleNode
    | ModelCanvasEllipseNode
    | ModelCanvasRectNode
    | ModelCanvasPolygonNode
    | ModelCanvasPolylineNode
    | ModelCanvasPathNode
)


class ModelCanvasEntity(RealtimeProtocolModel):
    id: NodeIdentifier
    name: Annotated[NonEmptyText, StringConstraints(max_length=80)]
    description: Annotated[NonEmptyText, StringConstraints(max_length=240)]
    node_ids: list[NodeIdentifier] = Field(min_length=1, max_length=8)
    facts: list[Annotated[NonEmptyText, StringConstraints(max_length=160)]] = Field(
        default_factory=list, max_length=3
    )

    @model_validator(mode="after")
    def node_references_are_unique(self) -> ModelCanvasEntity:
        if len(self.node_ids) != len(set(self.node_ids)):
            raise ValueError("entity nodeIds must be unique")
        return self


class ModelCanvasMotion(RealtimeProtocolModel):
    id: SegmentIdentifier
    node_id: NodeIdentifier
    property: CanvasAnimationProperty
    from_value: float = Field(allow_inf_nan=False)
    to_value: float = Field(allow_inf_nan=False)
    duration_ms: int = Field(ge=600, le=20_000)
    delay_ms: int = Field(default=0, ge=0, le=2_500)
    easing: CanvasEasing = CanvasEasing.EASE_IN_OUT
    autoreverse: bool = True

    @model_validator(mode="after")
    def motion_values_are_safe(self) -> ModelCanvasMotion:
        CanvasAnimateOperation.model_validate(
            {
                "operation": "animate",
                "nodeId": self.node_id,
                "property": self.property,
                "fromValue": self.from_value,
                "toValue": self.to_value,
                "durationMs": self.duration_ms,
                "delayMs": self.delay_ms,
                "easing": self.easing,
                "repeat": True,
                "autoreverse": self.autoreverse,
            }
        )
        return self


class _ModelCanvasInteractionBase(RealtimeProtocolModel):
    id: SegmentIdentifier
    trigger_node_id: NodeIdentifier


class ModelCanvasRevealInteraction(_ModelCanvasInteractionBase):
    action: Literal["reveal"]
    target_node_id: NodeIdentifier
    bounds: Literal[None]


class ModelCanvasToggleVisibilityInteraction(_ModelCanvasInteractionBase):
    action: Literal["toggleVisibility"]
    target_node_id: NodeIdentifier
    bounds: Literal[None]


class ModelCanvasSelectInteraction(_ModelCanvasInteractionBase):
    action: Literal["select"]
    target_node_id: NodeIdentifier
    bounds: Literal[None]


class ModelCanvasDragWithinInteraction(_ModelCanvasInteractionBase):
    action: Literal["dragWithin"]
    target_node_id: NodeIdentifier
    bounds: CanvasBounds


class ModelCanvasResetInteraction(_ModelCanvasInteractionBase):
    action: Literal["reset"]
    target_node_id: Literal[None]
    bounds: Literal[None]


class ModelCanvasExploreInteraction(_ModelCanvasInteractionBase):
    action: Literal["explore"]
    target_node_id: NodeIdentifier
    reveal_node_ids: list[NodeIdentifier] = Field(min_length=1, max_length=4)
    animate_node_ids: list[NodeIdentifier] = Field(max_length=4)
    exclusive: bool
    bounds: Literal[None]

    @model_validator(mode="before")
    @classmethod
    def _coerce_explore(cls, data: object) -> object:
        if isinstance(data, dict):
            d = dict(data)
            if "triggerEntityId" in d:
                if "triggerNodeId" not in d:
                    d["triggerNodeId"] = d["triggerEntityId"]
                d.pop("triggerEntityId", None)
            if "targetEntityId" in d:
                if "targetNodeId" not in d:
                    d["targetNodeId"] = d["targetEntityId"]
                d.pop("targetEntityId", None)
            if "targetNodeId" not in d and "triggerNodeId" in d:
                d["targetNodeId"] = d["triggerNodeId"]
            if "revealEntityIds" in d:
                if not d.get("revealNodeIds"):
                    d["revealNodeIds"] = d["revealEntityIds"]
                d.pop("revealEntityIds", None)
            if not d.get("revealNodeIds") and (
                d.get("targetNodeId") or d.get("triggerNodeId")
            ):
                d["revealNodeIds"] = [d.get("targetNodeId") or d.get("triggerNodeId")]
            if "revealNodeIds" in d:
                d["revealNodeIds"] = list(dict.fromkeys(d["revealNodeIds"]))[:4]
            if "animateEntityIds" in d:
                if not d.get("animateNodeIds"):
                    d["animateNodeIds"] = d["animateEntityIds"]
                d.pop("animateEntityIds", None)
            if not d.get("animateNodeIds"):
                d["animateNodeIds"] = []
            else:
                d["animateNodeIds"] = list(dict.fromkeys(d["animateNodeIds"]))[:4]
            if "exclusive" not in d:
                d["exclusive"] = True
            if "bounds" not in d:
                d["bounds"] = None
            return d
        return data

    @model_validator(mode="after")
    def references_are_unique(self) -> ModelCanvasExploreInteraction:
        if len(self.reveal_node_ids) != len(set(self.reveal_node_ids)):
            raise ValueError("explore revealNodeIds must be unique")
        if len(self.animate_node_ids) != len(set(self.animate_node_ids)):
            raise ValueError("explore animateNodeIds must be unique")
        return self


type ModelCanvasInteraction = (
    ModelCanvasExploreInteraction
    | ModelCanvasRevealInteraction
    | ModelCanvasToggleVisibilityInteraction
    | ModelCanvasSelectInteraction
    | ModelCanvasDragWithinInteraction
    | ModelCanvasResetInteraction
)


class ModelCanvasCue(RealtimeProtocolModel):
    """Internal compact scene transaction used by fixtures and compilation."""

    id: SegmentIdentifier
    anchor: AvaPhraseAnchor
    scene_id: NodeIdentifier
    nodes: list[ModelCanvasNode] = Field(min_length=1, max_length=96)
    interactions: list[ModelCanvasInteraction] = Field(max_length=16)
    motions: list[ModelCanvasMotion] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def references_are_local_and_unambiguous(self) -> ModelCanvasCue:
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("model canvas node ids must be unique per cue")
        known = set(node_ids)
        parent_by_id = {node.id: node.parent_id for node in self.nodes}
        for node in self.nodes:
            if node.parent_id is not None and node.parent_id not in known:
                raise ValueError(f"canvas parent {node.parent_id!r} is not in the cue")
            if node.parent_id == node.id:
                raise ValueError("canvas node cannot parent itself")
        visiting: set[NodeIdentifier] = set()
        visited: set[NodeIdentifier] = set()

        def visit(node_id: NodeIdentifier) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError("canvas parent relationships cannot contain cycles")
            visiting.add(node_id)
            parent_id = parent_by_id[node_id]
            if parent_id is not None:
                visit(parent_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)
        interaction_ids = [interaction.id for interaction in self.interactions]
        if len(interaction_ids) != len(set(interaction_ids)):
            raise ValueError("model canvas interaction ids must be unique per cue")
        node_by_id = {node.id: node for node in self.nodes}
        for node in self.nodes:
            if node.parent_id is None:
                continue
            if node_by_id[node.parent_id].kind != ModelCanvasNodeKind.GROUP:
                raise ValueError("canvas parent must reference a group node")
        for interaction in self.interactions:
            if interaction.trigger_node_id not in known:
                raise ValueError("canvas interaction trigger is not in the cue")
            if (
                interaction.target_node_id is not None
                and interaction.target_node_id not in known
            ):
                raise ValueError("canvas interaction target is not in the cue")
            if isinstance(interaction, ModelCanvasExploreInteraction):
                referenced = {
                    *interaction.reveal_node_ids,
                    *interaction.animate_node_ids,
                }
                if not referenced.issubset(known):
                    raise ValueError("explore interaction references unknown nodes")
                if any(
                    node_id != interaction.target_node_id
                    and node_id != interaction.trigger_node_id
                    and not node_by_id[node_id].hidden
                    for node_id in interaction.reveal_node_ids
                ):
                    raise ValueError("explore reveal targets must begin hidden")
            if node_by_id[interaction.trigger_node_id].hidden:
                raise ValueError("canvas interaction trigger must be visible")
            if (
                interaction.action == ModelCanvasInteractionAction.REVEAL
                and interaction.target_node_id is not None
                and not node_by_id[interaction.target_node_id].hidden
            ):
                raise ValueError("reveal interaction target must begin hidden")
            if (
                interaction.action == ModelCanvasInteractionAction.TOGGLE_VISIBILITY
                and interaction.trigger_node_id == interaction.target_node_id
            ):
                raise ValueError(
                    "toggleVisibility trigger and target must be different nodes"
                )
        motion_ids = [motion.id for motion in self.motions]
        if len(motion_ids) != len(set(motion_ids)):
            raise ValueError("model canvas motion ids must be unique")
        if any(motion.node_id not in known for motion in self.motions):
            raise ValueError("canvas motion references an unknown node")
        return self


class ModelCanvasDraft(RealtimeProtocolModel):
    """Strict specialist-authored scene draft before renderer compilation."""

    scene_id: NodeIdentifier
    title: Annotated[NonEmptyText, StringConstraints(max_length=100)] | None = None
    summary: Annotated[NonEmptyText, StringConstraints(max_length=320)] | None = None
    visual_type: ModelCanvasVisualType = ModelCanvasVisualType.FREEFORM
    topology: ModelCanvasTopology = ModelCanvasTopology.SPATIAL
    nodes: list[ModelCanvasNode] = Field(min_length=1, max_length=96)
    interactions: list[ModelCanvasInteraction] = Field(max_length=16)
    motions: list[ModelCanvasMotion] = Field(default_factory=list, max_length=16)
    entities: list[ModelCanvasEntity] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def graph_is_renderer_safe(self) -> ModelCanvasDraft:
        ModelCanvasCue.model_validate(
            {
                "id": "draft_graph_validation",
                "anchor": {"phrase": "canvas", "occurrence": 0},
                "sceneId": self.scene_id,
                "nodes": [
                    node.model_dump(mode="json", by_alias=True) for node in self.nodes
                ],
                "interactions": [
                    interaction.model_dump(mode="json", by_alias=True)
                    for interaction in self.interactions
                ],
                "motions": [
                    motion.model_dump(mode="json", by_alias=True)
                    for motion in self.motions
                ],
            }
        )
        known = {node.id for node in self.nodes}
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("canvas entity ids must be unique")
        for entity in self.entities:
            if not set(entity.node_ids).issubset(known):
                raise ValueError("canvas entity references an unknown node")
        return self


class ModelCanvasPlanEntity(RealtimeProtocolModel):
    id: NodeIdentifier
    label: Annotated[NonEmptyText, StringConstraints(max_length=120)]
    role: ModelCanvasDesignRole = ModelCanvasDesignRole.PRIMARY_OBJECT
    form: str = "sphere"
    visual_role: str = "primary"
    surface_style: str = "smooth"
    archetype: str | None = None
    preferred_region: str | None = None
    parent_entity_id: NodeIdentifier | None = None
    description: Annotated[NonEmptyText, StringConstraints(max_length=240)] | None = (
        None
    )
    facts: list[str] = Field(default_factory=list, max_length=12)
    details: list[str] = Field(default_factory=list, max_length=12)
    color_hint: str | None = None


def _model_text_typography(
    role: ModelCanvasDesignRole,
) -> tuple[float, str]:
    try:
        return {
            ModelCanvasDesignRole.TITLE: (38.0, "bold"),
            ModelCanvasDesignRole.PRIMARY_LABEL: (29.0, "bold"),
            ModelCanvasDesignRole.SECONDARY_LABEL: (25.0, "medium"),
            ModelCanvasDesignRole.EXPLANATORY_NOTE: (22.0, "regular"),
            ModelCanvasDesignRole.INTERACTIVE_CONTROL: (24.0, "bold"),
        }[role]
    except KeyError as error:
        raise ValueError("text nodes require a text designRole") from error


class ModelCanvasCompiler:
    """Compile compact model drafts into strict protocol-v3 renderer objects."""

    def __init__(self) -> None:
        self._scene_revisions: dict[str, int] = defaultdict(int)

    def _compile_cue(self, cue: ModelCanvasCue) -> AvaCanvasCue:
        base_revision = self._scene_revisions[cue.scene_id]
        revision = base_revision + 1
        self._scene_revisions[cue.scene_id] = revision
        ordered_nodes = _parent_first_canvas_nodes(cue.nodes)
        create_operations = [_compile_create_operation(node) for node in ordered_nodes]
        entrance_operations = [
            _compile_entrance_animation(node, index=index)
            for index, node in enumerate(ordered_nodes)
            if not node.hidden
            and node.kind != ModelCanvasNodeKind.GROUP
            and node.design_role
            in {
                ModelCanvasDesignRole.TITLE,
                ModelCanvasDesignRole.PRIMARY_OBJECT,
                ModelCanvasDesignRole.INTERACTIVE_CONTROL,
            }
        ]
        entrance_operations = entrance_operations[:12]
        motion_operations = [_compile_motion(motion) for motion in cue.motions]
        transaction = AvaCanvasTransaction.model_validate(
            {
                "id": f"{cue.id}_tx",
                "sceneId": cue.scene_id,
                "baseRevision": base_revision,
                "revision": revision,
                "operations": [
                    operation.model_dump(mode="json", by_alias=True)
                    for operation in (
                        *create_operations,
                        *entrance_operations,
                        *motion_operations,
                    )
                ],
                "stateVariables": [],
                "bindings": [
                    _compile_interaction(interaction).model_dump(
                        mode="json",
                        by_alias=True,
                    )
                    for interaction in cue.interactions
                ],
            }
        )
        return AvaCanvasCue(
            id=cue.id,
            anchor=cue.anchor,
            transaction=transaction,
        )

    def compile_canvas_draft(
        self,
        draft: ModelCanvasDraft,
        *,
        transaction_id: NodeIdentifier,
    ) -> AvaCanvasTransaction:
        cue = ModelCanvasCue.model_validate(
            {
                "id": transaction_id,
                "anchor": {"phrase": "canvas", "occurrence": 0},
                "sceneId": draft.scene_id,
                "nodes": [
                    node.model_dump(mode="json", by_alias=True) for node in draft.nodes
                ],
                "interactions": [
                    interaction.model_dump(mode="json", by_alias=True)
                    for interaction in draft.interactions
                ],
                "motions": [
                    motion.model_dump(mode="json", by_alias=True)
                    for motion in draft.motions
                ],
            }
        )
        return self._compile_cue(cue).transaction


def _parent_first_canvas_nodes(
    nodes: list[ModelCanvasNode],
) -> list[ModelCanvasNode]:
    """Return a stable parent-before-child order required by create operations."""

    by_id = {node.id: node for node in nodes}
    ordered: list[ModelCanvasNode] = []
    emitted: set[NodeIdentifier] = set()

    def emit(node: ModelCanvasNode) -> None:
        if node.id in emitted:
            return
        parent_id = node.parent_id
        if parent_id is not None:
            emit(by_id[parent_id])
        emitted.add(node.id)
        ordered.append(node)

    for node in nodes:
        emit(node)
    return ordered


def compile_model_canvas_draft(
    draft: ModelCanvasDraft,
    *,
    transaction_id: NodeIdentifier,
) -> AvaCanvasTransaction:
    return ModelCanvasCompiler().compile_canvas_draft(
        draft,
        transaction_id=transaction_id,
    )


def _compile_create_operation(node: ModelCanvasNode) -> CanvasCreateOperation:
    identity_transform = {
        "translateX": 0.0,
        "translateY": 0.0,
        "scale": 1.0,
        "rotationDegrees": 0.0,
    }
    style = _renderer_style(node)
    pivot = node.pivot
    payload: dict[str, object] = {
        "id": node.id,
        "parentId": node.parent_id,
        "label": node.label,
        "visible": not node.hidden,
        "transform": identity_transform,
        "style": style,
        "kind": node.kind,
    }
    identity_transform["pivotX"] = pivot.x if pivot is not None else 0.0
    identity_transform["pivotY"] = pivot.y if pivot is not None else 0.0
    if isinstance(node, ModelCanvasTextNode):
        fallback_size, fallback_weight = _model_text_typography(node.design_role)
        font_size = node.font_size if node.font_size is not None else fallback_size
        font_weight = (
            node.font_weight if node.font_weight is not None else fallback_weight
        )
        text_anchor = node.text_anchor if node.text_anchor is not None else "middle"
        payload.update(
            {
                "x": node.x,
                "y": node.y,
                "text": node.text,
                "fontSize": font_size,
                "fontWeight": font_weight,
                "textAnchor": text_anchor,
            }
        )
    elif isinstance(node, (ModelCanvasLineNode, ModelCanvasArrowNode)):
        payload.update(
            {
                "start": {"x": node.x, "y": node.y},
                "end": {"x": node.x2, "y": node.y2},
            }
        )
    elif isinstance(node, ModelCanvasCircleNode):
        payload.update(
            {
                "center": {"x": node.x, "y": node.y},
                "radius": node.radius,
            }
        )
    elif isinstance(node, ModelCanvasEllipseNode):
        payload.update(
            {
                "center": {"x": node.x, "y": node.y},
                "radiusX": node.radius_x,
                "radiusY": node.radius_y,
            }
        )
    elif isinstance(node, ModelCanvasRectNode):
        payload.update(
            {
                "x": node.x,
                "y": node.y,
                "width": node.width,
                "height": node.height,
                "cornerRadius": node.corner_radius,
            }
        )
    elif isinstance(node, (ModelCanvasPolygonNode, ModelCanvasPolylineNode)):
        payload["points"] = [
            point.model_dump(mode="json", by_alias=True) for point in node.points
        ]
    elif isinstance(node, ModelCanvasPathNode):
        payload["pathData"] = node.path_data
    return CanvasCreateOperation.model_validate(
        {
            "operation": "create",
            "node": payload,
        }
    )


def _compile_entrance_animation(
    node: ModelCanvasNode,
    *,
    index: int,
) -> CanvasAnimateOperation:
    """Create deterministic, local presentation timing for a new scene.

    The specialist describes meaning and layout. Ava owns timing so network
    jitter and provider output can never drive animation frames.
    """

    role = node.design_role
    property_name = (
        CanvasAnimationProperty.SCALE
        if role
        in {
            ModelCanvasDesignRole.PRIMARY_OBJECT,
            ModelCanvasDesignRole.SECONDARY_OBJECT,
            ModelCanvasDesignRole.INTERACTIVE_CONTROL,
        }
        else CanvasAnimationProperty.OPACITY
    )
    from_value = 0.92 if property_name == CanvasAnimationProperty.SCALE else 0.0
    delay_ms = min(2_500, 45 * index)
    if role == ModelCanvasDesignRole.TITLE:
        delay_ms = 0
    return CanvasAnimateOperation.model_validate(
        {
            "operation": "animate",
            "nodeId": node.id,
            "property": property_name,
            "fromValue": from_value,
            "toValue": 1.0,
            "durationMs": (
                280 if property_name == CanvasAnimationProperty.SCALE else 220
            ),
            "delayMs": delay_ms,
            "easing": CanvasEasing.EASE_OUT,
            "repeat": False,
            "autoreverse": False,
        }
    )


def _compile_motion(motion: ModelCanvasMotion) -> CanvasAnimateOperation:
    return CanvasAnimateOperation.model_validate(
        {
            "operation": "animate",
            "nodeId": motion.node_id,
            "property": motion.property,
            "fromValue": motion.from_value,
            "toValue": motion.to_value,
            "durationMs": motion.duration_ms,
            "delayMs": motion.delay_ms,
            "easing": motion.easing,
            "repeat": True,
            "autoreverse": motion.autoreverse,
        }
    )


def _renderer_style(
    node: ModelCanvasNode,
) -> dict[str, object]:
    kind = node.kind
    role = node.style
    if kind == ModelCanvasNodeKind.TEXT:
        text_fill = {
            ModelCanvasStyleRole.MUTED: CanvasColorToken.MUTED,
            ModelCanvasStyleRole.POSITIVE: CanvasColorToken.POSITIVE,
        }.get(role, CanvasColorToken.INK)
        base: dict[str, object] = {
            "fill": text_fill,
            "stroke": CanvasColorToken.TRANSPARENT,
            "strokeWidth": 0.0,
            "opacity": 1.0,
            "strokeCap": "round",
        }
    else:
        fill, stroke = {
            ModelCanvasStyleRole.PRIMARY: (
                CanvasColorToken.ACCENT,
                CanvasColorToken.INK,
            ),
            ModelCanvasStyleRole.SECONDARY: (
                CanvasColorToken.ACCENT_SOFT,
                CanvasColorToken.INK,
            ),
            ModelCanvasStyleRole.OUTLINE: (
                CanvasColorToken.TRANSPARENT,
                CanvasColorToken.INK,
            ),
            ModelCanvasStyleRole.LABEL: (
                CanvasColorToken.TRANSPARENT,
                CanvasColorToken.INK,
            ),
            ModelCanvasStyleRole.POSITIVE: (
                CanvasColorToken.POSITIVE,
                CanvasColorToken.POSITIVE,
            ),
            ModelCanvasStyleRole.WARNING: (
                CanvasColorToken.WARNING,
                CanvasColorToken.WARNING,
            ),
            ModelCanvasStyleRole.MUTED: (
                CanvasColorToken.TRANSPARENT,
                CanvasColorToken.MUTED,
            ),
        }[role]
        base = {
            "fill": fill,
            "stroke": stroke,
            "strokeWidth": 3.0 if role == ModelCanvasStyleRole.OUTLINE else 2.0,
            "opacity": 1.0,
            "strokeCap": "round",
        }
    authored = node.visual_style
    base.update(
        {
            "fillColor": authored.fill_color if authored is not None else None,
            "strokeColor": authored.stroke_color if authored is not None else None,
            "gradient": (
                authored.gradient.model_dump(mode="json", by_alias=True)
                if authored is not None and authored.gradient is not None
                else None
            ),
            "radialGradient": (
                authored.radial_gradient.model_dump(mode="json", by_alias=True)
                if authored is not None and authored.radial_gradient is not None
                else None
            ),
            "shadow": (
                authored.shadow.model_dump(mode="json", by_alias=True)
                if authored is not None and authored.shadow is not None
                else None
            ),
        }
    )
    if authored is not None and authored.stroke_width is not None:
        base["strokeWidth"] = authored.stroke_width
    if authored is not None and authored.opacity is not None:
        base["opacity"] = authored.opacity
    return base


def _compile_interaction(
    interaction: ModelCanvasInteraction,
) -> CanvasInteractionBinding:
    if interaction.action == ModelCanvasInteractionAction.RESET:
        action: (
            CanvasResetAction
            | CanvasDragWithinAction
            | CanvasExploreAction
            | CanvasSelectAction
            | dict[str, object]
        ) = CanvasResetAction(kind="reset")
    elif isinstance(interaction, ModelCanvasExploreInteraction):
        reveals = [
            r for r in interaction.reveal_node_ids if r != interaction.target_node_id
        ]
        if reveals:
            action = CanvasExploreAction.model_validate(
                {
                    "kind": "explore",
                    "targetNodeId": interaction.target_node_id,
                    "revealNodeIds": reveals,
                    "animateNodeIds": interaction.animate_node_ids,
                    "exclusive": interaction.exclusive,
                }
            )
        else:
            action = CanvasSelectAction.model_validate(
                {
                    "kind": "select",
                    "targetNodeId": interaction.target_node_id,
                }
            )
    elif interaction.action == ModelCanvasInteractionAction.DRAG_WITHIN:
        if interaction.target_node_id is None or interaction.bounds is None:
            raise ValueError("validated dragWithin interaction lost its payload")
        action = CanvasDragWithinAction.model_validate(
            {
                "kind": "dragWithin",
                "targetNodeId": interaction.target_node_id,
                "bounds": interaction.bounds.model_dump(
                    mode="json",
                    by_alias=True,
                ),
            }
        )
    else:
        if interaction.target_node_id is None:
            raise ValueError("validated canvas interaction lost its target")
        action = {
            "kind": interaction.action,
            "targetNodeId": interaction.target_node_id,
        }
    return CanvasInteractionBinding.model_validate(
        {
            "id": f"{interaction.id}_binding",
            "nodeId": interaction.trigger_node_id,
            "event": (
                "drag"
                if interaction.action == ModelCanvasInteractionAction.DRAG_WITHIN
                else "tap"
            ),
            "action": (
                action.model_dump(mode="json", by_alias=True)
                if isinstance(
                    action,
                    (CanvasResetAction, CanvasDragWithinAction, CanvasExploreAction),
                )
                else action
            ),
        }
    )


def _model_cue_from_runtime(cue: AvaCanvasCue) -> dict[str, object]:
    transaction = cue.transaction
    if transaction.state_variables:
        raise ValueError("fake model-plan conversion does not support canvas state")
    nodes: list[dict[str, object]] = []
    for operation in transaction.operations:
        if not isinstance(operation, CanvasCreateOperation):
            raise ValueError(
                "fake model-plan conversion supports only create operations"
            )
        nodes.append(_model_node_from_runtime(operation))
    interactions: list[dict[str, object]] = []
    for binding in transaction.bindings:
        action = binding.action
        if not isinstance(
            action,
            (
                CanvasRevealAction,
                CanvasToggleVisibilityAction,
                CanvasSelectAction,
                CanvasDragWithinAction,
                CanvasResetAction,
            ),
        ):
            raise ValueError(
                "fake model-plan conversion encountered an unsupported interaction"
            )
        if isinstance(action, CanvasDragWithinAction) and binding.event != "drag":
            raise ValueError("fake dragWithin interaction requires a drag event")
        if not isinstance(action, CanvasDragWithinAction) and binding.event != "tap":
            raise ValueError("fake non-drag interaction requires a tap event")
        interactions.append(
            {
                "id": binding.id.removesuffix("_binding"),
                "triggerNodeId": binding.node_id,
                "action": action.kind,
                "targetNodeId": getattr(action, "target_node_id", None),
                "bounds": (
                    action.bounds.model_dump(mode="json", by_alias=True)
                    if isinstance(action, CanvasDragWithinAction)
                    else None
                ),
            }
        )
    return {
        "id": cue.id,
        "anchor": cue.anchor.model_dump(mode="json", by_alias=True),
        "sceneId": transaction.scene_id,
        "nodes": nodes,
        "interactions": interactions,
    }


def _model_node_from_runtime(operation: CanvasCreateOperation) -> dict[str, object]:
    node = operation.node
    if node.transform.model_dump(mode="json", by_alias=True) != {
        "translateX": 0.0,
        "translateY": 0.0,
        "scale": 1.0,
        "rotationDegrees": 0.0,
    }:
        raise ValueError("fake model-plan conversion requires identity transforms")
    payload: dict[str, object] = {
        "id": node.id,
        "parentId": node.parent_id,
        "kind": node.kind,
        "label": node.label,
        "style": _model_style_from_runtime(node.kind, node.style.fill),
        "designRole": _model_design_role_from_runtime(node),
        "hidden": not node.visible,
    }
    if isinstance(node, CanvasTextNode):
        payload.update({"x": node.x, "y": node.y, "text": node.text})
    elif isinstance(node, CanvasLineNode):
        payload.update(
            {
                "x": node.start.x,
                "y": node.start.y,
                "x2": node.end.x,
                "y2": node.end.y,
            }
        )
    elif isinstance(node, CanvasCircleNode):
        payload.update({"x": node.center.x, "y": node.center.y, "radius": node.radius})
    elif isinstance(node, CanvasEllipseNode):
        payload.update(
            {
                "x": node.center.x,
                "y": node.center.y,
                "radiusX": node.radius_x,
                "radiusY": node.radius_y,
            }
        )
    elif isinstance(node, CanvasRectNode):
        payload.update(
            {
                "x": node.x,
                "y": node.y,
                "width": node.width,
                "height": node.height,
                "cornerRadius": node.corner_radius,
            }
        )
    elif isinstance(node, CanvasPointsNode):
        payload["points"] = [
            point.model_dump(mode="json", by_alias=True) for point in node.points
        ]
    elif isinstance(node, CanvasPathNode):
        payload["pathData"] = node.path_data
    return payload


def _model_style_from_runtime(
    kind: str,
    fill: CanvasColorToken,
) -> ModelCanvasStyleRole:
    if kind == "text":
        if fill == CanvasColorToken.MUTED:
            return ModelCanvasStyleRole.MUTED
        if fill == CanvasColorToken.POSITIVE:
            return ModelCanvasStyleRole.POSITIVE
        return ModelCanvasStyleRole.LABEL
    return {
        CanvasColorToken.ACCENT: ModelCanvasStyleRole.PRIMARY,
        CanvasColorToken.ACCENT_SOFT: ModelCanvasStyleRole.SECONDARY,
        CanvasColorToken.POSITIVE: ModelCanvasStyleRole.POSITIVE,
        CanvasColorToken.WARNING: ModelCanvasStyleRole.WARNING,
        CanvasColorToken.TRANSPARENT: ModelCanvasStyleRole.OUTLINE,
        CanvasColorToken.INK: ModelCanvasStyleRole.OUTLINE,
        CanvasColorToken.MUTED: ModelCanvasStyleRole.MUTED,
        CanvasColorToken.PAPER: ModelCanvasStyleRole.SECONDARY,
    }[fill]


def _model_design_role_from_runtime(
    node: CanvasGroupNode
    | CanvasTextNode
    | CanvasLineNode
    | CanvasCircleNode
    | CanvasEllipseNode
    | CanvasRectNode
    | CanvasPointsNode
    | CanvasPathNode,
) -> ModelCanvasDesignRole:
    if isinstance(node, CanvasGroupNode):
        return ModelCanvasDesignRole.SECONDARY_OBJECT
    if isinstance(node, CanvasTextNode):
        if node.font_size >= 34:
            return ModelCanvasDesignRole.TITLE
        if node.font_size >= 28:
            return ModelCanvasDesignRole.PRIMARY_LABEL
        if node.font_size >= 24:
            return ModelCanvasDesignRole.SECONDARY_LABEL
        return ModelCanvasDesignRole.EXPLANATORY_NOTE
    if isinstance(node, CanvasLineNode):
        return ModelCanvasDesignRole.CONNECTOR
    return (
        ModelCanvasDesignRole.PRIMARY_OBJECT
        if node.style.fill == CanvasColorToken.ACCENT
        else ModelCanvasDesignRole.SECONDARY_OBJECT
    )


def _substring_positions(text: str, phrase: str) -> tuple[int, ...]:
    positions: list[int] = []
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return tuple(positions)
        positions.append(index)
        start = index + max(1, len(phrase))
