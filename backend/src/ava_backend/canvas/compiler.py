from __future__ import annotations

from typing import Sequence

from .specialist import CanvasSpecialistInvalidError
from ..realtime.model_plan import (
    ModelCanvasDraft,
    ModelCanvasEntity,
    ModelCanvasInteraction,
    ModelCanvasMotion,
    ModelCanvasNode,
    ModelCanvasPlanEntity,
    ModelCanvasPlanInteraction,
    ModelCanvasPlanRelation,
    ModelCanvasTopology,
    ModelCanvasVisualType,
    _validate_specialist_canvas_behavior,
)


class CanvasSceneCompiler:
    """Domain-agnostic visual scene compiler and protocol validator.

    Validates and normalizes specialist-authored declarative vector scenes into
    valid, publication-grade Protocol-v3 transactions without imposing artificial
    layout templates or procedural redesign.
    """

    def __init__(self) -> None:
        pass

    def compile_plan(
        self,
        plan: object,
        *,
        scene_id: str,
    ) -> ModelCanvasDraft:
        """Compile a specialist draft into a validated ModelCanvasDraft."""
        raw_nodes = getattr(plan, "nodes", [])
        if not raw_nodes:
            raise CanvasSpecialistInvalidError(
                "Visual specialist returned no drawable scene"
            )

        title = str(
            getattr(plan, "title", "Interactive Visual") or "Interactive Visual"
        )
        summary = str(getattr(plan, "summary", "") or "")
        visual_type = getattr(plan, "visual_type", ModelCanvasVisualType.FREEFORM)
        topology = getattr(plan, "topology", ModelCanvasTopology.SPATIAL)
        entities = getattr(plan, "entities", [])
        relations = getattr(plan, "relations", [])
        interactions_input = getattr(plan, "interactions", [])
        motions_input = getattr(plan, "motions", [])

        return self._heal_and_compile_declarative_scene(
            raw_nodes=raw_nodes,
            entities=entities,
            relations=relations,
            interactions_input=interactions_input,
            motions_input=motions_input,
            title=title,
            summary=summary,
            visual_type=visual_type,
            topology=topology,
            scene_id=scene_id,
        )

    def _heal_and_compile_declarative_scene(
        self,
        *,
        raw_nodes: Sequence[ModelCanvasNode],
        entities: Sequence[ModelCanvasEntity | ModelCanvasPlanEntity],
        relations: Sequence[ModelCanvasPlanRelation],
        interactions_input: Sequence[
            ModelCanvasInteraction | ModelCanvasPlanInteraction
        ],
        motions_input: Sequence[ModelCanvasMotion],
        title: str,
        summary: str,
        visual_type: ModelCanvasVisualType,
        topology: ModelCanvasTopology,
        scene_id: str,
    ) -> ModelCanvasDraft:
        # Auto-heal parent relationships in raw_nodes:
        # If parent_id references a non-group, unknown node, or self, reset it to None
        raw_node_ids = {getattr(n, "id", None) for n in raw_nodes if hasattr(n, "id")}
        raw_group_ids = {
            getattr(n, "id", None)
            for n in raw_nodes
            if str(getattr(n, "kind", "")) in ("group", "ModelCanvasNodeKind.GROUP")
        }
        for node in raw_nodes:
            pid = getattr(node, "parent_id", None)
            if pid is not None and (
                pid not in raw_node_ids
                or pid not in raw_group_ids
                or pid == getattr(node, "id", None)
            ):
                try:
                    object.__setattr__(node, "parent_id", None)
                except Exception:
                    pass

        # Validate node relationships and interaction references
        _validate_specialist_canvas_behavior(raw_nodes, interactions_input)

        nodes_dict: list[dict[str, object]] = []
        for node in raw_nodes:
            n_dict = (
                node
                if isinstance(node, dict)
                else node.model_dump(mode="json", by_alias=True)
            )
            kind = n_dict.get("kind")
            if kind == "circle":
                r = float(n_dict.get("radius", 30))
                r = min(r, 140)
                n_dict["radius"] = r
                if "x" in n_dict and n_dict["x"] is not None:
                    n_dict["x"] = max(40 + r, min(960 - r, float(n_dict["x"])))
                if "y" in n_dict and n_dict["y"] is not None:
                    n_dict["y"] = max(40 + r, min(560 - r, float(n_dict["y"])))
            elif kind == "ellipse":
                rx = float(n_dict.get("radiusX", n_dict.get("radius_x", 30)))
                ry = float(n_dict.get("radiusY", n_dict.get("radius_y", 20)))
                rx = min(rx, 200)
                ry = min(ry, 140)
                if "radiusX" in n_dict:
                    n_dict["radiusX"] = rx
                if "radius_x" in n_dict:
                    n_dict["radius_x"] = rx
                if "radiusY" in n_dict:
                    n_dict["radiusY"] = ry
                if "radius_y" in n_dict:
                    n_dict["radius_y"] = ry
                if "x" in n_dict and n_dict["x"] is not None:
                    n_dict["x"] = max(40 + rx, min(960 - rx, float(n_dict["x"])))
                if "y" in n_dict and n_dict["y"] is not None:
                    n_dict["y"] = max(40 + ry, min(560 - ry, float(n_dict["y"])))
            elif kind == "rect":
                w = float(n_dict.get("width", 100))
                h = float(n_dict.get("height", 60))
                w = min(w, 900)
                h = min(h, 500)
                n_dict["width"] = w
                n_dict["height"] = h
                if "x" in n_dict and n_dict["x"] is not None:
                    n_dict["x"] = max(40, min(960 - w, float(n_dict["x"])))
                if "y" in n_dict and n_dict["y"] is not None:
                    n_dict["y"] = max(40, min(560 - h, float(n_dict["y"])))
            elif kind in ("line", "arrow"):
                if "x" in n_dict and n_dict["x"] is not None:
                    n_dict["x"] = max(40, min(960, float(n_dict["x"])))
                if "y" in n_dict and n_dict["y"] is not None:
                    n_dict["y"] = max(40, min(560, float(n_dict["y"])))
                if "x2" in n_dict and n_dict["x2"] is not None:
                    n_dict["x2"] = max(40, min(960, float(n_dict["x2"])))
                if "y2" in n_dict and n_dict["y2"] is not None:
                    n_dict["y2"] = max(40, min(560, float(n_dict["y2"])))
            elif kind == "text":
                if "x" in n_dict and n_dict["x"] is not None:
                    n_dict["x"] = max(40, min(920, float(n_dict["x"])))
                if "y" in n_dict and n_dict["y"] is not None:
                    n_dict["y"] = max(30, min(550, float(n_dict["y"])))
            nodes_dict.append(n_dict)

        # Compiled Interactions: preserve explicit specialist interactions
        compiled_interactions = [
            i if isinstance(i, dict) else i.model_dump(mode="json", by_alias=True)
            for i in interactions_input
        ]

        dict_node_ids = {str(n.get("id")) for n in nodes_dict}
        dict_group_ids = {
            str(n.get("id")) for n in nodes_dict if n.get("kind") == "group"
        }
        for n in nodes_dict:
            if "style" not in n or not n["style"]:
                n["style"] = "primary"
            if "hidden" not in n or n["hidden"] is None:
                n["hidden"] = False
            pid = n.get("parentId")
            if pid is not None and (
                str(pid) not in dict_group_ids or str(pid) == str(n.get("id"))
            ):
                n["parentId"] = None

        # Ensure explore reveal targets begin hidden and triggers begin visible
        for i_dict in compiled_interactions:
            trigger_id = i_dict.get("triggerNodeId")
            if trigger_id and str(trigger_id) in dict_node_ids:
                for n in nodes_dict:
                    if n.get("id") == trigger_id:
                        n["hidden"] = False
            if i_dict.get("action") == "explore":
                target_id = i_dict.get("targetNodeId")
                reveals = [
                    r
                    for r in i_dict.get("revealNodeIds", [])
                    if r != target_id and r != trigger_id and str(r) in dict_node_ids
                ]
                i_dict["revealNodeIds"] = reveals
                for r in reveals:
                    for n in nodes_dict:
                        if n.get("id") == r:
                            n["hidden"] = True
                if target_id and str(target_id) in dict_node_ids:
                    for n in nodes_dict:
                        if n.get("id") == target_id:
                            n["hidden"] = False

        # Compiled Motions
        compiled_motions = [
            m if isinstance(m, dict) else m.model_dump(mode="json", by_alias=True)
            for m in motions_input
        ]

        # Compiled Educational Hotspots (Entities)
        compiled_entities: list[dict[str, object]] = []
        for entity in entities:
            if isinstance(entity, ModelCanvasPlanEntity):
                matching_ids = [
                    str(n["id"])
                    for n in nodes_dict
                    if str(n["id"]).startswith(entity.id)
                ]
                compiled_entities.append(
                    {
                        "id": entity.id,
                        "name": str(entity.label)[:80],
                        "nodeIds": matching_ids or [str(nodes_dict[0]["id"])],
                        "description": str(entity.description or entity.label)[:240],
                        "facts": [str(f)[:160] for f in entity.details[:4]],
                    }
                )
            else:
                e_dict = (
                    entity
                    if isinstance(entity, dict)
                    else entity.model_dump(mode="json", by_alias=True)
                )
                compiled_entities.append(e_dict)

        return ModelCanvasDraft.model_validate(
            {
                "sceneId": scene_id,
                "title": title,
                "summary": summary,
                "visualType": visual_type,
                "topology": topology,
                "nodes": nodes_dict[:96],
                "interactions": compiled_interactions[:16],
                "motions": compiled_motions[:16],
                "entities": compiled_entities[:16],
            }
        )
