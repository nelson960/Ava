from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from html import escape

from ..realtime.model_plan import (
    ModelCanvasDraft,
    ModelCanvasEntity,
    compile_model_canvas_draft,
    compile_model_canvas_revision,
)
from .protocol import (
    AvaCanvasTransaction,
    CanvasExploreAction,
    CanvasSelectAction,
    CanvasSubmitOutcomeAction,
)
from .validation import CanvasSceneValidator, ValidatedCanvasScene


class CanvasInteractionRejectionCode(StrEnum):
    NO_ACTIVE_SCENE = "noActiveScene"
    SCENE_MISMATCH = "sceneMismatch"
    SCENE_REVISION_MISMATCH = "sceneRevisionMismatch"
    INTERACTION_REVISION_STALE = "interactionRevisionStale"
    BINDING_UNKNOWN = "bindingUnknown"
    BINDING_LOCAL_ONLY = "bindingLocalOnly"


class CanvasInteractionRejected(ValueError):
    """A client-reported interaction does not match the canonical scene."""

    def __init__(
        self,
        code: CanvasInteractionRejectionCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RecordedCanvasInteraction:
    scene_id: str
    scene_revision: int
    interaction_revision: int
    binding_id: str
    action: str
    target_id: str
    target_label: str | None
    target_description: str | None
    declared_value: str | None


@dataclass(frozen=True, slots=True)
class PreparedCanvasDraft:
    transaction: AvaCanvasTransaction
    scene: ValidatedCanvasScene
    draft: ModelCanvasDraft
    base_scene_id: str | None
    base_revision: int


class CanvasSessionState:
    """Authoritative, bounded canvas state shared across conversation turns.

    Visual explore feedback, reveal, toggle, and drag behavior stays on the
    device. Declared explore, select, and submitted-outcome events may also be
    reported so the next spoken turn can refer to the selected semantic
    entity. Their meaning is resolved from the server-validated binding rather
    than from arbitrary client text.
    """

    def __init__(self) -> None:
        self._validator = CanvasSceneValidator()
        self._scene: ValidatedCanvasScene | None = None
        self._draft: ModelCanvasDraft | None = None
        self._last_interaction: RecordedCanvasInteraction | None = None

    @property
    def scene(self) -> ValidatedCanvasScene | None:
        return self._scene

    @property
    def draft(self) -> ModelCanvasDraft | None:
        return self._draft

    @property
    def last_interaction(self) -> RecordedCanvasInteraction | None:
        return self._last_interaction

    def apply_draft(
        self,
        draft: ModelCanvasDraft,
        *,
        transaction_id: str,
    ) -> AvaCanvasTransaction:
        prepared = self.prepare_draft(draft, transaction_id=transaction_id)
        self.commit_prepared(prepared)
        return prepared.transaction

    def prepare_draft(
        self,
        draft: ModelCanvasDraft,
        *,
        transaction_id: str,
    ) -> PreparedCanvasDraft:
        """Compile and validate a revision without exposing it as active."""

        current = self._scene
        transaction: AvaCanvasTransaction
        if current is None:
            transaction = compile_model_canvas_draft(
                draft,
                transaction_id=transaction_id,
            )
        else:
            transaction = compile_model_canvas_revision(
                draft,
                transaction_id=transaction_id,
                current=current,
            )
        scene = self._validator.apply(transaction, current)
        return PreparedCanvasDraft(
            transaction=transaction,
            scene=scene,
            draft=draft,
            base_scene_id=current.id if current is not None else None,
            base_revision=current.revision if current is not None else 0,
        )

    def commit_prepared(self, prepared: PreparedCanvasDraft) -> None:
        current = self._scene
        current_scene_id = current.id if current is not None else None
        current_revision = current.revision if current is not None else 0
        if (
            current_scene_id != prepared.base_scene_id
            or current_revision != prepared.base_revision
        ):
            raise ValueError(
                "canonical canvas changed before the prepared revision committed"
            )
        self._scene = prepared.scene
        self._draft = prepared.draft

    def preview_draft(
        self,
        draft: ModelCanvasDraft,
        *,
        transaction_id: str,
    ) -> AvaCanvasTransaction:
        """Compile and validate a revision without mutating canonical state."""

        return self.prepare_draft(
            draft,
            transaction_id=transaction_id,
        ).transaction

    def record_interaction(
        self,
        *,
        scene_id: str,
        scene_revision: int,
        interaction_revision: int,
        binding_id: str,
    ) -> RecordedCanvasInteraction:
        scene = self._scene
        if scene is None:
            raise CanvasInteractionRejected(
                CanvasInteractionRejectionCode.NO_ACTIVE_SCENE,
                "canvasInteraction requires an active canonical scene",
            )
        if scene.id != scene_id:
            raise CanvasInteractionRejected(
                CanvasInteractionRejectionCode.SCENE_MISMATCH,
                f'active scene "{scene.id}" cannot accept "{scene_id}"',
            )
        if scene.revision != scene_revision:
            raise CanvasInteractionRejected(
                CanvasInteractionRejectionCode.SCENE_REVISION_MISMATCH,
                f"expected canvas revision {scene.revision}, received {scene_revision}",
            )
        previous_revision = (
            self._last_interaction.interaction_revision
            if (
                self._last_interaction is not None
                and self._last_interaction.scene_id == scene_id
            )
            else 0
        )
        if interaction_revision <= previous_revision:
            raise CanvasInteractionRejected(
                CanvasInteractionRejectionCode.INTERACTION_REVISION_STALE,
                "canvas interactionRevision must increase monotonically",
            )

        binding = scene.bindings.get(binding_id)
        if binding is None:
            raise CanvasInteractionRejected(
                CanvasInteractionRejectionCode.BINDING_UNKNOWN,
                f'canvas binding "{binding_id}" does not exist',
            )
        action = binding.action
        if isinstance(action, (CanvasSelectAction, CanvasExploreAction)):
            target_id = action.target_node_id
            target_label = scene.nodes[target_id].label
            entity = self._entity_for_node(target_id)
            target_description = entity.description if entity is not None else None
            declared_value = None
        elif isinstance(action, CanvasSubmitOutcomeAction):
            target_id = action.outcome_id
            target_label = None
            target_description = None
            declared_value = action.value
        else:
            raise CanvasInteractionRejected(
                CanvasInteractionRejectionCode.BINDING_LOCAL_ONLY,
                f'canvas binding "{binding_id}" is local-only and must not be sent',
            )

        recorded = RecordedCanvasInteraction(
            scene_id=scene_id,
            scene_revision=scene_revision,
            interaction_revision=interaction_revision,
            binding_id=binding_id,
            action=action.kind,
            target_id=target_id,
            target_label=target_label,
            target_description=target_description,
            declared_value=declared_value,
        )
        self._last_interaction = recorded
        return recorded

    def render_realtime_context(self) -> str | None:
        scene = self._scene
        if scene is None:
            return None

        lines = [
            '<active_canvas trust="validated_scene_data" '
            f'scene_id="{escape(scene.id, quote=True)}" '
            f'revision="{scene.revision}">',
            "  <semantic_entities>",
        ]
        included_characters = 0
        included_entities = 0
        draft = self._draft
        entities = draft.entities if draft is not None else []
        for entity in entities:
            entry_characters = (
                len(entity.id) + len(entity.name) + len(entity.description)
            )
            if (
                included_entities >= 12
                or included_characters + entry_characters > 1_200
            ):
                break
            lines.append(
                "    <entity "
                f'id="{escape(entity.id, quote=True)}" '
                f'node_ids="{escape(",".join(entity.node_ids), quote=True)}" '
                f'name="{escape(entity.name, quote=True)}">'
                f"{escape(entity.description, quote=False)}</entity>"
            )
            included_entities += 1
            included_characters += entry_characters
        if not entities:
            for node in list(scene.nodes.values())[:12]:
                lines.append(
                    "    <entity "
                    f'id="{escape(node.id, quote=True)}" '
                    f'node_ids="{escape(node.id, quote=True)}" '
                    f'name="{escape(node.label, quote=True)}">'
                    f"{escape(node.label, quote=False)}</entity>"
                )
        lines.append("  </semantic_entities>")
        interaction = self._last_interaction
        if interaction is not None and interaction.scene_id == scene.id:
            lines.append(
                "  <last_semantic_interaction "
                f'action="{escape(interaction.action, quote=True)}" '
                f'target_id="{escape(interaction.target_id, quote=True)}">'
                f"{escape(interaction.target_description or interaction.target_label or interaction.declared_value or '', quote=False)}"
                "</last_semantic_interaction>"
            )
        lines.append("</active_canvas>")
        return "\n".join(lines)

    def _entity_for_node(self, node_id: str) -> ModelCanvasEntity | None:
        draft = self._draft
        if draft is None:
            return None
        return next(
            (entity for entity in draft.entities if node_id in entity.node_ids),
            None,
        )
