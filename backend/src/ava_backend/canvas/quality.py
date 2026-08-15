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
    INSUFFICIENT_DETAIL = "insufficientDetail"
    CLIPPED_CONTENT = "clippedContent"
    MISSING_ENTITY = "missingEntity"
    SMALL_TRIGGER = "smallTrigger"
    TEXT_OVERLAP = "textOverlap"
    WEAK_COMPOSITION = "weakComposition"
    WEAK_VISUAL_STYLE = "weakVisualStyle"
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
        if len(draft.nodes) < 8:
            issues.append(
                CanvasQualityIssue(
                    code=CanvasQualityIssueCode.INSUFFICIENT_DETAIL,
                    node_ids=(),
                    message=(
                        "the scene has fewer than eight purposeful visual layers; "
                        "add recognizable structure, labels, and interaction feedback"
                    ),
                )
            )
        for node in draft.nodes:
            value = bounds.get(node.id)
            if (
                node.hidden
                or value is None
                or isinstance(node, (ModelCanvasGroupNode, ModelCanvasPathNode))
            ):
                continue
            safe_top = 20 if isinstance(node, ModelCanvasTextNode) else 40
            if value[0] < 40 or value[1] < safe_top or value[2] > 960 or value[3] > 560:
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.CLIPPED_CONTENT,
                        node_ids=(node.id,),
                        message=(
                            f'visible node "{node.id}" leaves the safe 40..960 by '
                            "40..560 viewport"
                        ),
                    )
                )
        interactive_targets: set[str] = set()
        for interaction in draft.interactions:
            target_id = interaction.target_node_id
            if target_id is not None:
                interactive_targets.add(target_id)
            if isinstance(interaction, ModelCanvasExploreInteraction):
                interactive_targets.add(interaction.target_node_id)

        entity_nodes = {
            node_id for entity in draft.entities for node_id in entity.node_ids
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

        for interaction in draft.interactions:
            trigger_bounds = bounds.get(interaction.trigger_node_id)
            if trigger_bounds is None:
                continue
            width = trigger_bounds[2] - trigger_bounds[0]
            height = trigger_bounds[3] - trigger_bounds[1]
            if width < 44 or height < 44:
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.SMALL_TRIGGER,
                        node_ids=(interaction.trigger_node_id,),
                        message=(
                            f'interaction trigger "{interaction.trigger_node_id}" '
                            f"is only {width:.0f} by {height:.0f}; make its group "
                            "or visible geometry at least 44 by 44"
                        ),
                    )
                )

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
                if smaller > 0 and overlap / smaller > 0.12:
                    issues.append(
                        CanvasQualityIssue(
                            code=CanvasQualityIssueCode.TEXT_OVERLAP,
                            node_ids=(first.id, second.id),
                            message=(
                                f'text "{first.id}" overlaps "{second.id}"; '
                                "author distinct final positions"
                            ),
                        )
                    )

        visible_bounds = [
            bounds[node.id]
            for node in draft.nodes
            if not node.hidden
            and not isinstance(node, (ModelCanvasGroupNode, ModelCanvasTextNode))
            and node.id in bounds
        ]
        if len(draft.nodes) >= 8 and visible_bounds:
            composition = _union(visible_bounds)
            if (
                composition[2] - composition[0] < 520
                or composition[3] - composition[1] < 260
            ):
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.WEAK_COMPOSITION,
                        node_ids=(),
                        message=(
                            "the visible composition uses too little of the 1000 by "
                            "600 viewport; enlarge and balance the meaningful visual"
                        ),
                    )
                )

        authored_colors = {
            color
            for node in draft.nodes
            if node.visual_style is not None
            for color in (
                node.visual_style.fill_color,
                node.visual_style.stroke_color,
            )
            if color is not None
        }
        gradient_count = sum(
            node.visual_style is not None
            and (
                node.visual_style.gradient is not None
                or node.visual_style.radial_gradient is not None
            )
            for node in draft.nodes
        )
        if len(draft.nodes) >= 8 and len(authored_colors) + gradient_count < 3:
            issues.append(
                CanvasQualityIssue(
                    code=CanvasQualityIssueCode.WEAK_VISUAL_STYLE,
                    node_ids=(),
                    message=(
                        "the scene relies on generic defaults; author a cohesive "
                        "high-contrast palette with at least three purposeful colors"
                    ),
                )
            )

        object_bounds = [
            value
            for node_id, value in bounds.items()
            if not isinstance(
                next(node for node in draft.nodes if node.id == node_id),
                (
                    ModelCanvasLineNode,
                    ModelCanvasArrowNode,
                    ModelCanvasTextNode,
                    ModelCanvasGroupNode,
                ),
            )
            and _area(value) < 1000 * 600 * 0.6
        ]
        for node in draft.nodes:
            if (
                not isinstance(node, (ModelCanvasLineNode, ModelCanvasArrowNode))
                or node.hidden
            ):
                continue
            start = (node.x, node.y)
            end = (node.x2, node.y2)
            if not any(
                _near_bounds(start, value, 32) for value in object_bounds
            ) or not any(_near_bounds(end, value, 32) for value in object_bounds):
                issues.append(
                    CanvasQualityIssue(
                        code=CanvasQualityIssueCode.DISCONNECTED_CONNECTOR,
                        node_ids=(node.id,),
                        message=(
                            f'connector "{node.id}" does not terminate near meaningful '
                            "objects at both ends"
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
            "title": 38,
            "primaryLabel": 29,
            "secondaryLabel": 25,
            "explanatoryNote": 22,
            "interactiveControl": 24,
        }[str(node.design_role)]
        lines = node.text.splitlines()
        width = min(
            520.0, max(24.0, max(len(line) for line in lines) * font_size * 0.54)
        )
        height = len(lines) * font_size * 1.2
        return (node.x - width / 2, node.y - height, node.x + width / 2, node.y)
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
