from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from ..realtime.model_plan import (
    ModelCanvasCircleNode,
    ModelCanvasDraft,
    ModelCanvasEllipseNode,
    ModelCanvasExploreInteraction,
    ModelCanvasGroupNode,
    ModelCanvasArrowNode,
    ModelCanvasLineNode,
    ModelCanvasPathNode,
    ModelCanvasPolygonNode,
    ModelCanvasPolylineNode,
    ModelCanvasRectNode,
    ModelCanvasTextNode,
)


class CanvasQualityIssueCode(StrEnum):
    CLIPPED_CONTENT = "clippedContent"
    MISSING_ENTITY = "missingEntity"
    SMALL_TRIGGER = "smallTrigger"
    TEXT_OVERLAP = "textOverlap"
    DISCONNECTED_CONNECTOR = "disconnectedConnector"


@dataclass(frozen=True, slots=True)
class CanvasQualityIssue:
    code: CanvasQualityIssueCode
    node_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class CanvasQualityReport:
    issues: tuple[CanvasQualityIssue, ...]

    @property
    def accepted(self) -> bool:
        return not self.issues

    def repair_brief(self) -> str:
        return "\n".join(
            f"- {issue.code}: {issue.message}" for issue in self.issues[:12]
        )


_Bounds = tuple[float, float, float, float]


class CanvasQualityEvaluator:
    """Deterministic pre-publication checks for an authored canvas draft."""

    def evaluate(self, draft: ModelCanvasDraft) -> CanvasQualityReport:
        bounds = _resolved_bounds(draft)
        issues: list[CanvasQualityIssue] = []

        # 1. Check for clipped content (outside 1000x600 viewport)
        for node in draft.nodes:
            value = bounds.get(node.id)
            if (
                node.hidden
                or value is None
                or isinstance(node, (ModelCanvasGroupNode, ModelCanvasPathNode))
            ):
                continue
            # Background / backdrop / landscape bands spanning the width or height are allowed
            if (value[2] - value[0] >= 800) or (value[3] - value[1] >= 450):
                continue
            if node.id in (
                "bg",
                "background",
                "sky",
                "ground",
                "backdrop",
                "strata",
                "base",
            ):
                continue
            if value[0] < -60 or value[1] < -60 or value[2] > 1060 or value[3] > 660:
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.CLIPPED_CONTENT,
                        node_ids=(node.id,),
                        message=(
                            f'visible node "{node.id}" extends significantly outside the 1000x600 viewport'
                        ),
                    )
                )

        # 2. Check for missing semantic entities for interactive targets
        interactive_targets: set[str] = set()
        for interaction in draft.interactions:
            target_id = getattr(interaction, "target_node_id", None)
            if target_id is None and isinstance(interaction, dict):
                target_id = interaction.get("target_node_id") or interaction.get(
                    "targetNodeId"
                )
            if target_id is not None:
                interactive_targets.add(target_id)
            if isinstance(interaction, ModelCanvasExploreInteraction):
                interactive_targets.add(interaction.target_node_id)

        entity_nodes = {
            node_id
            for entity in draft.entities
            for node_id in getattr(entity, "node_ids", getattr(entity, "nodeIds", []))
        }
        for node_id in sorted(interactive_targets - entity_nodes):
            issues.append(
                CanvasQualityIssue(
                    code=CanvasQualityIssueCode.MISSING_ENTITY,
                    node_ids=(node_id,),
                    message=(
                        f'interactive node "{node_id}" needs a semantic entity '
                        "with a conversational description"
                    ),
                )
            )

        # 3. Check for small touch triggers
        for interaction in draft.interactions:
            trigger_id = getattr(interaction, "trigger_node_id", None)
            if trigger_id is None and isinstance(interaction, dict):
                trigger_id = interaction.get("trigger_node_id") or interaction.get(
                    "triggerNodeId"
                )
            if not trigger_id:
                continue
            trigger_bounds = bounds.get(trigger_id)
            if trigger_bounds is None:
                continue
            width = trigger_bounds[2] - trigger_bounds[0]
            height = trigger_bounds[3] - trigger_bounds[1]
            if max(width, height) < 32 or (width < 24 and height < 24):
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.SMALL_TRIGGER,
                        node_ids=(trigger_id,),
                        message=(
                            f'interaction trigger "{trigger_id}" '
                            f"is only {width:.0f} by {height:.0f}; make its group "
                            "or visible geometry at least 32 by 32"
                        ),
                    )
                )

        # 4. Check for severe text overlap
        visible_text = [
            node
            for node in draft.nodes
            if isinstance(node, ModelCanvasTextNode) and not node.hidden
        ]
        for index, first in enumerate(visible_text):
            first_bounds = bounds.get(first.id)
            if first_bounds is None:
                continue
            for second in visible_text[index + 1 :]:
                second_bounds = bounds.get(second.id)
                if second_bounds is None:
                    continue
                overlap = _intersection_area(first_bounds, second_bounds)
                smaller = min(_area(first_bounds), _area(second_bounds))
                if smaller > 0 and overlap / smaller > 0.60:
                    issues.append(
                        CanvasQualityIssue(
                            code=CanvasQualityIssueCode.TEXT_OVERLAP,
                            node_ids=(first.id, second.id),
                            message=(
                                f'text "{first.id}" overlaps "{second.id}"; '
                                "author distinct positions"
                            ),
                        )
                    )

        # 5. Check for disconnected standalone connectors
        all_bounds = list(bounds.values())
        for node in draft.nodes:
            if (
                not isinstance(node, (ModelCanvasLineNode, ModelCanvasArrowNode))
                or node.hidden
            ):
                continue
            start = (node.x, node.y)
            end = (node.x2, node.y2)
            if all_bounds and (
                not any(_near_bounds(start, value, 64) for value in all_bounds)
                and not any(_near_bounds(end, value, 64) for value in all_bounds)
            ):
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.DISCONNECTED_CONNECTOR,
                        node_ids=(node.id,),
                        message=(
                            f'connector "{node.id}" does not terminate near any meaningful '
                            "objects or labels"
                        ),
                    )
                )

        return CanvasQualityReport(issues=tuple(issues[:12]))


def _resolved_bounds(draft: ModelCanvasDraft) -> dict[str, _Bounds]:
    direct = {
        node.id: value
        for node in draft.nodes
        if (value := _node_bounds(node)) is not None
    }
    children: dict[str, list[str]] = {}
    for node in draft.nodes:
        if node.parent_id is not None:
            children.setdefault(node.parent_id, []).append(node.id)

    def resolve(node_id: str) -> _Bounds | None:
        own = direct.get(node_id)
        descendants = [
            value
            for child_id in children.get(node_id, ())
            if (value := resolve(child_id)) is not None
        ]
        if own is not None:
            descendants.append(own)
        if not descendants:
            return None
        value = _union(descendants)
        direct[node_id] = value
        return value

    for node in draft.nodes:
        resolve(node.id)
    return direct


def _node_bounds(node: object) -> _Bounds | None:
    if isinstance(node, ModelCanvasTextNode):
        font_size = {
            "title": 34,
            "primaryLabel": 24,
            "secondaryLabel": 20,
            "explanatoryNote": 16,
            "interactiveControl": 20,
        }.get(str(getattr(node, "design_role", "primaryLabel")), 22)
        lines = node.text.splitlines()
        width = min(
            520.0, max(24.0, max(len(line) for line in lines) * font_size * 0.50)
        )
        height = len(lines) * font_size * 1.1
        # Check text anchor if present
        anchor = getattr(node, "text_anchor", None)
        if (
            anchor == "middle"
            or "middle" in getattr(node, "label", "").lower()
            or (node.x > 400 and node.x < 600 and node.y < 100)
        ):
            x1 = node.x - width / 2
            x2 = node.x + width / 2
        elif anchor == "end":
            x1 = node.x - width
            x2 = node.x
        else:
            x1 = node.x
            x2 = node.x + width
        return (x1, node.y - height, x2, node.y + 4)
    if isinstance(node, ModelCanvasLineNode):
        return (
            min(node.x, node.x2),
            min(node.y, node.y2),
            max(node.x, node.x2),
            max(node.y, node.y2),
        )
    if isinstance(node, ModelCanvasCircleNode):
        return (
            node.x - node.radius,
            node.y - node.radius,
            node.x + node.radius,
            node.y + node.radius,
        )
    if isinstance(node, ModelCanvasEllipseNode):
        return (
            node.x - node.radius_x,
            node.y - node.radius_y,
            node.x + node.radius_x,
            node.y + node.radius_y,
        )
    if isinstance(node, ModelCanvasRectNode):
        return (node.x, node.y, node.x + node.width, node.y + node.height)
    if isinstance(node, (ModelCanvasPolygonNode, ModelCanvasPolylineNode)):
        xs = [point.x for point in node.points]
        ys = [point.y for point in node.points]
        return (min(xs), min(ys), max(xs), max(ys))
    if isinstance(node, ModelCanvasPathNode):
        numbers = [
            float(value)
            for value in re.findall(
                r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)",
                node.path_data,
            )
        ]
        if len(numbers) < 2:
            return None
        xs = numbers[0::2]
        ys = numbers[1::2]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _union(bounds: list[_Bounds]) -> _Bounds:
    return (
        min(value[0] for value in bounds),
        min(value[1] for value in bounds),
        max(value[2] for value in bounds),
        max(value[3] for value in bounds),
    )


def _area(bounds: _Bounds) -> float:
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _intersection_area(first: _Bounds, second: _Bounds) -> float:
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0,
        min(first[3], second[3]) - max(first[1], second[1]),
    )


def _near_bounds(point: tuple[float, float], bounds: _Bounds, distance: float) -> bool:
    return (
        bounds[0] - distance <= point[0] <= bounds[2] + distance
        and bounds[1] - distance <= point[1] <= bounds[3] + distance
    )
