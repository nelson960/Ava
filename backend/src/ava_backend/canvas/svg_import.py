from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Sequence

from ..canvas.protocol import (
    CanvasGradientStop,
    CanvasLinearGradient,
    CanvasPoint,
    CanvasRadialGradient,
    CanvasShadow,
)
from ..realtime.model_plan import (
    ModelCanvasCircleNode,
    ModelCanvasDraft,
    ModelCanvasEllipseNode,
    ModelCanvasEntity,
    ModelCanvasExploreInteraction,
    ModelCanvasGroupNode,
    ModelCanvasInteraction,
    ModelCanvasLineNode,
    ModelCanvasMotion,
    ModelCanvasNode,
    ModelCanvasPathNode,
    ModelCanvasPlanEntity,
    ModelCanvasPolygonNode,
    ModelCanvasPolylineNode,
    ModelCanvasRectNode,
    ModelCanvasTextNode,
    ModelCanvasVisualStyle,
)

# Standard CSS named colors mapping to 6-digit hex
_CSS_COLORS: dict[str, str] = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#008000",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "silver": "#C0C0C0",
    "gray": "#808080",
    "grey": "#808080",
    "maroon": "#800000",
    "olive": "#808000",
    "purple": "#800080",
    "teal": "#008080",
    "navy": "#000080",
    "orange": "#FFA500",
    "brown": "#A52A2A",
    "gold": "#FFD700",
    "pink": "#FFC0CB",
    "coral": "#FF7F50",
    "turquoise": "#40E0D0",
    "violet": "#EE82EE",
    "indigo": "#4B0082",
    "lime": "#00FF00",
    "khaki": "#F0E68C",
    "plum": "#DDA0DD",
    "salmon": "#FA8072",
    "tan": "#D2B48C",
    "skyblue": "#87CEEB",
    "lightgreen": "#90EE90",
    "lightblue": "#ADD8E6",
    "darkblue": "#00008B",
    "darkgreen": "#006400",
    "darkred": "#8B0000",
}


def _blend_hex_color(hex_color: str, opacity: float, bg_hex: str = "#CDE8F1") -> str:
    if opacity >= 0.98:
        return hex_color
    try:
        r1, g1, b1 = (
            int(hex_color[1:3], 16),
            int(hex_color[3:5], 16),
            int(hex_color[5:7], 16),
        )
        r2, g2, b2 = int(bg_hex[1:3], 16), int(bg_hex[3:5], 16), int(bg_hex[5:7], 16)
        r = int(r1 * opacity + r2 * (1.0 - opacity))
        g = int(g1 * opacity + g2 * (1.0 - opacity))
        b = int(b1 * opacity + b2 * (1.0 - opacity))
        return f"#{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"
    except Exception:
        return hex_color


def _normalize_hex_color(val: str | None) -> str | None:
    if not val:
        return None
    val = val.strip()
    if val.lower() in ("none", "transparent"):
        return None
    if val.lower() in _CSS_COLORS:
        return _CSS_COLORS[val.lower()]
    if val.startswith("#"):
        hex_digits = val[1:]
        if len(hex_digits) == 3:
            return f"#{hex_digits[0] * 2}{hex_digits[1] * 2}{hex_digits[2] * 2}".upper()
        if len(hex_digits) == 6:
            return f"#{hex_digits}".upper()
        if len(hex_digits) == 8:
            return f"#{hex_digits[:6]}".upper()
    return None


def _parse_style_attribute(style_str: str | None) -> dict[str, str]:
    if not style_str:
        return {}
    styles: dict[str, str] = {}
    for part in style_str.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, value = part.split(":", 1)
        styles[key.strip().lower()] = value.strip()
    return styles


def _normalize_stops(raw_stops: list[CanvasGradientStop]) -> list[CanvasGradientStop]:
    if not raw_stops:
        return [
            CanvasGradientStop(offset=0.0, color="#3B82F6"),
            CanvasGradientStop(offset=1.0, color="#1D4ED8"),
        ]
    sorted_stops = sorted(raw_stops, key=lambda s: s.offset)
    deduped: list[CanvasGradientStop] = []
    last_offset = -1.0
    for s in sorted_stops:
        off = max(0.0, min(1.0, s.offset))
        if off > last_offset:
            deduped.append(CanvasGradientStop(offset=off, color=s.color))
            last_offset = off
    if len(deduped) < 2:
        deduped = [
            CanvasGradientStop(offset=0.0, color=raw_stops[0].color),
            CanvasGradientStop(
                offset=1.0,
                color=raw_stops[-1].color if len(raw_stops) > 1 else raw_stops[0].color,
            ),
        ]
    if len(deduped) > 4:
        deduped = [
            deduped[0],
            deduped[len(deduped) // 3],
            deduped[2 * len(deduped) // 3],
            deduped[-1],
        ]
    deduped[0] = CanvasGradientStop(offset=0.0, color=deduped[0].color)
    deduped[-1] = CanvasGradientStop(offset=1.0, color=deduped[-1].color)
    for i in range(1, len(deduped) - 1):
        if deduped[i].offset <= deduped[i - 1].offset:
            deduped[i] = CanvasGradientStop(
                offset=min(0.99, deduped[i - 1].offset + 0.05), color=deduped[i].color
            )
    return deduped[:4]


def _clean_node_id(raw_id: str | None, fallback_prefix: str, index: int) -> str:
    if raw_id:
        clean = re.sub(r"[^a-z0-9_]", "_", raw_id.strip().lower())
        clean = re.sub(r"_+", "_", clean).strip("_")
        if clean and clean[0].isalpha():
            return clean[:48]
        if clean:
            return f"n_{clean}"[:48]
    return f"{fallback_prefix}_{index}"


def parse_svg_to_canvas_draft(
    svg_content: str,
    *,
    scene_id: str,
    title: str = "Canvas Scene",
    summary: str = "Interactive visual explanation",
    entities: Sequence[ModelCanvasEntity | ModelCanvasPlanEntity | dict[str, Any]]
    | None = None,
    motions: Sequence[ModelCanvasMotion | dict[str, Any]] | None = None,
) -> ModelCanvasDraft:
    """Parse standard SVG XML markup into a validated ModelCanvasDraft."""
    clean_svg = svg_content.strip()
    if "```" in clean_svg:
        match = re.search(r"<svg[\s\S]*?</svg>", clean_svg, re.IGNORECASE)
        if match:
            clean_svg = match.group(0)

    clean_svg = re.sub(r"<script[\s\S]*?</script>", "", clean_svg, flags=re.IGNORECASE)
    clean_svg = re.sub(
        r"<foreignObject[\s\S]*?</foreignObject>", "", clean_svg, flags=re.IGNORECASE
    )

    try:
        root = ET.fromstring(clean_svg)
    except Exception as e:
        raise ValueError(f"Invalid SVG markup: {e}") from e

    def local_tag(elem: ET.Element) -> str:
        tag = elem.tag
        if "}" in tag:
            return tag.split("}", 1)[1].lower()
        return tag.lower()

    if local_tag(root) != "svg":
        raise ValueError("Root element must be <svg>")

    # 1. Parse Defs
    gradients: dict[str, CanvasLinearGradient] = {}
    radial_gradients: dict[str, CanvasRadialGradient] = {}
    shadows: dict[str, CanvasShadow] = {}

    for defs in root.iter():
        if local_tag(defs) != "defs":
            continue
        for child in defs:
            tag = local_tag(child)
            cid = child.attrib.get("id")
            if not cid:
                continue

            if tag == "lineargradient":
                try:
                    x1 = float(child.attrib.get("x1", "0").rstrip("%px"))
                    y1 = float(child.attrib.get("y1", "0").rstrip("%px"))
                    x2 = float(child.attrib.get("x2", "1").rstrip("%px"))
                    y2 = float(child.attrib.get("y2", "0").rstrip("%px"))
                except ValueError:
                    x1, y1, x2, y2 = 0.0, 0.0, 1000.0, 0.0

                stops: list[CanvasGradientStop] = []
                for stop in child:
                    if local_tag(stop) == "stop":
                        offset_str = stop.attrib.get("offset", "0").rstrip("%")
                        try:
                            offset_val = float(offset_str)
                            if offset_val > 1.0:
                                offset_val /= 100.0
                        except ValueError:
                            offset_val = 0.0
                        color_val = (
                            stop.attrib.get("stop-color")
                            or stop.attrib.get("stopColor")
                            or "#3B82F6"
                        )
                        norm_color = _normalize_hex_color(color_val) or "#3B82F6"
                        stops.append(
                            CanvasGradientStop(
                                offset=max(0.0, min(1.0, offset_val)), color=norm_color
                            )
                        )

                norm_stops = _normalize_stops(stops)
                gradients[cid] = CanvasLinearGradient(
                    start=CanvasPoint(x=x1, y=y1),
                    end=CanvasPoint(x=x2, y=y2),
                    stops=norm_stops,
                )

            elif tag == "radialgradient":
                try:
                    cx = float(child.attrib.get("cx", "500").rstrip("%px"))
                    cy = float(child.attrib.get("cy", "300").rstrip("%px"))
                    r = float(child.attrib.get("r", "200").rstrip("%px"))
                except ValueError:
                    cx, cy, r = 500.0, 300.0, 200.0

                stops = []
                for stop in child:
                    if local_tag(stop) == "stop":
                        offset_str = stop.attrib.get("offset", "0").rstrip("%")
                        try:
                            offset_val = float(offset_str)
                            if offset_val > 1.0:
                                offset_val /= 100.0
                        except ValueError:
                            offset_val = 0.0
                        color_val = (
                            stop.attrib.get("stop-color")
                            or stop.attrib.get("stopColor")
                            or "#FFD54F"
                        )
                        norm_color = _normalize_hex_color(color_val) or "#FFD54F"
                        stops.append(
                            CanvasGradientStop(
                                offset=max(0.0, min(1.0, offset_val)), color=norm_color
                            )
                        )

                norm_stops = _normalize_stops(stops)
                radial_gradients[cid] = CanvasRadialGradient(
                    center=CanvasPoint(x=cx, y=cy),
                    radius=max(1.0, r),
                    stops=norm_stops,
                )

            elif tag == "filter":
                for fe in child:
                    if local_tag(fe) == "fedropshadow":
                        dx = float(fe.attrib.get("dx", "0"))
                        dy = float(fe.attrib.get("dy", "4"))
                        std = float(fe.attrib.get("stdDeviation", "6"))
                        color = (
                            _normalize_hex_color(
                                fe.attrib.get("flood-color", "#0F172A")
                            )
                            or "#0F172A"
                        )
                        op = float(fe.attrib.get("flood-opacity", "0.35"))
                        shadows[cid] = CanvasShadow.model_validate(
                            {
                                "color": color,
                                "blurRadius": max(0.0, min(32.0, std)),
                                "offsetX": max(-24.0, min(24.0, dx)),
                                "offsetY": max(-24.0, min(24.0, dy)),
                                "opacity": max(0.0, min(0.5, op)),
                            }
                        )

    # 2. Parse SVG Elements
    nodes: list[ModelCanvasNode] = []
    used_ids: set[str] = set()
    node_index = 0

    def get_element_styles(elem: ET.Element) -> dict[str, str]:
        styles = _parse_style_attribute(elem.attrib.get("style"))
        res: dict[str, str] = {}
        for attr in (
            "fill",
            "stroke",
            "stroke-width",
            "strokeWidth",
            "opacity",
            "filter",
            "display",
            "font-family",
            "font-size",
            "font-weight",
            "text-anchor",
            "textAnchor",
        ):
            val = elem.attrib.get(attr) or styles.get(attr.lower())
            if val is not None:
                res[attr.lower().replace("-", "_")] = val.strip()
        return res

    def merge_styles(parent: dict[str, str], local: dict[str, str]) -> dict[str, str]:
        merged = {**parent, **local}
        if "opacity" in parent and "opacity" in local:
            try:
                merged["opacity"] = str(
                    max(
                        0.0,
                        min(1.0, float(parent["opacity"]) * float(local["opacity"])),
                    )
                )
            except ValueError:
                pass
        return merged

    def resolve_style_from_dict(
        style_dict: dict[str, str],
    ) -> tuple[ModelCanvasVisualStyle | None, bool]:
        fill_val = style_dict.get("fill")
        stroke_val = style_dict.get("stroke")
        stroke_w = style_dict.get("stroke_width")
        opacity_val = style_dict.get("opacity")
        filter_val = style_dict.get("filter")
        display_val = style_dict.get("display")

        is_hidden = False
        if display_val in ("none", "hidden"):
            is_hidden = True

        v_fill: str | None = None
        v_grad: CanvasLinearGradient | None = None
        v_rgrad: CanvasRadialGradient | None = None

        if fill_val:
            fill_clean = fill_val.strip()
            if fill_clean.startswith("url(#") and fill_clean.endswith(")"):
                gid = fill_clean[5:-1]
                if gid in gradients:
                    v_grad = gradients[gid]
                elif gid in radial_gradients:
                    v_rgrad = radial_gradients[gid]
            elif fill_clean.lower() not in ("none", "transparent"):
                v_fill = _normalize_hex_color(fill_clean)

        v_stroke = _normalize_hex_color(stroke_val)
        v_stroke_width: float | None = None
        if stroke_w:
            try:
                v_stroke_width = max(0.0, min(16.0, float(stroke_w.rstrip("px"))))
            except ValueError:
                pass

        v_opacity: float | None = None
        if opacity_val:
            try:
                v_opacity = max(0.0, min(1.0, float(opacity_val)))
            except ValueError:
                pass

        v_shadow: CanvasShadow | None = None
        if filter_val:
            filter_clean = filter_val.strip()
            if filter_clean.startswith("url(#") and filter_clean.endswith(")"):
                fid = filter_clean[5:-1]
                if fid in shadows:
                    v_shadow = shadows[fid]
                elif "shadow" in fid.lower():
                    v_shadow = CanvasShadow.model_validate(
                        {
                            "color": "#0F172A",
                            "blurRadius": 6.0,
                            "offsetX": 0.0,
                            "offsetY": 4.0,
                            "opacity": 0.35,
                        }
                    )

        if not any(
            (
                v_fill,
                v_stroke,
                v_stroke_width,
                v_opacity is not None,
                v_grad,
                v_rgrad,
                v_shadow,
            )
        ):
            return None, is_hidden

        return (
            ModelCanvasVisualStyle.model_validate(
                {
                    "fillColor": v_fill,
                    "strokeColor": v_stroke,
                    "strokeWidth": v_stroke_width,
                    "opacity": v_opacity,
                    "gradient": v_grad.model_dump(by_alias=True) if v_grad else None,
                    "radialGradient": (
                        v_rgrad.model_dump(by_alias=True) if v_rgrad else None
                    ),
                    "shadow": v_shadow.model_dump(by_alias=True) if v_shadow else None,
                }
            ),
            is_hidden,
        )

    def generate_node_id(prefix: str, authored_id: str | None) -> str:
        nonlocal node_index
        node_index += 1
        cid = _clean_node_id(authored_id, prefix, node_index)
        while cid in used_ids:
            node_index += 1
            cid = f"{prefix}_{node_index}"
        used_ids.add(cid)
        return cid

    def walk(
        elem: ET.Element,
        current_parent: str | None = None,
        inherited_style: dict[str, str] | None = None,
    ) -> None:
        if len(nodes) >= 96:
            return
        tag = local_tag(elem)
        if tag in ("defs", "style", "metadata", "title", "desc"):
            return

        authored_id = elem.attrib.get("id")
        label = elem.attrib.get("label") or authored_id or tag

        local_s = get_element_styles(elem)
        merged_s = merge_styles(inherited_style or {}, local_s)
        v_style, is_hidden = resolve_style_from_dict(merged_s)

        if tag == "g":
            gid = generate_node_id("group", authored_id)
            nodes.append(
                ModelCanvasGroupNode.model_validate(
                    {
                        "id": gid,
                        "parentId": current_parent,
                        "label": label[:120],
                        "kind": "group",
                        "style": "primary",
                        "visualStyle": v_style.model_dump(by_alias=True)
                        if v_style
                        else None,
                        "hidden": is_hidden,
                    }
                )
            )
            for child in elem:
                walk(child, current_parent=gid, inherited_style=merged_s)
            return

        if tag == "path":
            path_data = elem.attrib.get("d", "").strip()
            if path_data:
                nid = generate_node_id("path", authored_id)
                nodes.append(
                    ModelCanvasPathNode.model_validate(
                        {
                            "id": nid,
                            "parentId": current_parent,
                            "label": label[:120],
                            "kind": "path",
                            "pathData": path_data[:2000],
                            "style": "primary",
                            "visualStyle": v_style.model_dump(by_alias=True)
                            if v_style
                            else None,
                            "hidden": is_hidden,
                        }
                    )
                )

        elif tag == "circle":
            try:
                cx = float(elem.attrib.get("cx", "500").rstrip("px"))
                cy = float(elem.attrib.get("cy", "300").rstrip("px"))
                r = float(elem.attrib.get("r", "50").rstrip("px"))
                nid = generate_node_id("circle", authored_id)
                nodes.append(
                    ModelCanvasCircleNode.model_validate(
                        {
                            "id": nid,
                            "parentId": current_parent,
                            "label": label[:120],
                            "kind": "circle",
                            "x": cx,
                            "y": cy,
                            "radius": max(1.0, r),
                            "style": "primary",
                            "visualStyle": v_style.model_dump(by_alias=True)
                            if v_style
                            else None,
                            "hidden": is_hidden,
                        }
                    )
                )
            except ValueError:
                pass

        elif tag == "ellipse":
            try:
                cx = float(elem.attrib.get("cx", "500").rstrip("px"))
                cy = float(elem.attrib.get("cy", "300").rstrip("px"))
                rx = float(elem.attrib.get("rx", "80").rstrip("px"))
                ry = float(elem.attrib.get("ry", "40").rstrip("px"))
                nid = generate_node_id("ellipse", authored_id)
                nodes.append(
                    ModelCanvasEllipseNode.model_validate(
                        {
                            "id": nid,
                            "parentId": current_parent,
                            "label": label[:120],
                            "kind": "ellipse",
                            "x": cx,
                            "y": cy,
                            "radiusX": max(1.0, rx),
                            "radiusY": max(1.0, ry),
                            "style": "primary",
                            "visualStyle": v_style.model_dump(by_alias=True)
                            if v_style
                            else None,
                            "hidden": is_hidden,
                        }
                    )
                )
            except ValueError:
                pass

        elif tag == "rect":
            try:
                x = float(elem.attrib.get("x", "0").rstrip("px"))
                y = float(elem.attrib.get("y", "0").rstrip("px"))
                w = float(elem.attrib.get("width", "100").rstrip("px"))
                h = float(elem.attrib.get("height", "60").rstrip("px"))
                rx = float(elem.attrib.get("rx", "0").rstrip("px"))
                # Strip framing border strokes on full-bleed background rectangles so artwork feels open
                if (
                    current_parent is None
                    and x <= 20
                    and y <= 20
                    and w >= 960
                    and h >= 560
                ):
                    if v_style is not None and v_style.stroke_color is not None:
                        v_style = v_style.model_copy(
                            update={"stroke_color": None, "stroke_width": None}
                        )
                nid = generate_node_id("rect", authored_id)
                nodes.append(
                    ModelCanvasRectNode.model_validate(
                        {
                            "id": nid,
                            "parentId": current_parent,
                            "label": label[:120],
                            "kind": "rect",
                            "x": x,
                            "y": y,
                            "width": max(1.0, w),
                            "height": max(1.0, h),
                            "cornerRadius": max(0.0, rx),
                            "style": "primary",
                            "visualStyle": v_style.model_dump(by_alias=True)
                            if v_style
                            else None,
                            "hidden": is_hidden,
                        }
                    )
                )
            except ValueError:
                pass

        elif tag == "line":
            try:
                x1 = float(elem.attrib.get("x1", "0").rstrip("px"))
                y1 = float(elem.attrib.get("y1", "0").rstrip("px"))
                x2 = float(elem.attrib.get("x2", "100").rstrip("px"))
                y2 = float(elem.attrib.get("y2", "100").rstrip("px"))
                nid = generate_node_id("line", authored_id)
                nodes.append(
                    ModelCanvasLineNode.model_validate(
                        {
                            "id": nid,
                            "parentId": current_parent,
                            "label": label[:120],
                            "kind": "line",
                            "x": x1,
                            "y": y1,
                            "x2": x2,
                            "y2": y2,
                            "style": "primary",
                            "visualStyle": v_style.model_dump(by_alias=True)
                            if v_style
                            else None,
                            "hidden": is_hidden,
                        }
                    )
                )
            except ValueError:
                pass

        elif tag in ("polygon", "polyline"):
            pts_str = elem.attrib.get("points", "").strip()
            if pts_str:
                raw_nums = [
                    float(n) for n in re.findall(r"[-+]?[0-9]*\.?[0-9]+", pts_str)
                ]
                pts: list[dict[str, float]] = []
                for i in range(0, len(raw_nums) - 1, 2):
                    pts.append({"x": raw_nums[i], "y": raw_nums[i + 1]})
                if len(pts) >= 2:
                    nid = generate_node_id(tag, authored_id)
                    kind = (
                        "polygon"
                        if (tag == "polygon" and len(pts) >= 3)
                        else "polyline"
                    )
                    node_cls = (
                        ModelCanvasPolygonNode
                        if kind == "polygon"
                        else ModelCanvasPolylineNode
                    )
                    nodes.append(
                        node_cls.model_validate(
                            {
                                "id": nid,
                                "parentId": current_parent,
                                "label": label[:120],
                                "kind": kind,
                                "points": pts[:64],
                                "style": "primary",
                                "visualStyle": v_style.model_dump(by_alias=True)
                                if v_style
                                else None,
                                "hidden": is_hidden,
                            }
                        )
                    )

        elif tag == "text":
            try:
                x = float(elem.attrib.get("x", "500").rstrip("px"))
                y = float(elem.attrib.get("y", "300").rstrip("px"))
                anchor_raw = merged_s.get("text_anchor") or merged_s.get("textanchor")
                anchor = (
                    "middle"
                    if anchor_raw == "middle"
                    else "end"
                    if anchor_raw in ("end", "right")
                    else "start"
                )

                font_size_val: float | None = None
                fs_str = merged_s.get("font_size") or merged_s.get("fontsize")
                if fs_str:
                    try:
                        font_size_val = max(8.0, min(96.0, float(fs_str.rstrip("px"))))
                    except ValueError:
                        pass

                fw_str = (
                    merged_s.get("font_weight") or merged_s.get("fontweight") or ""
                ).lower()
                font_weight = (
                    "bold"
                    if (
                        fw_str in ("bold", "700", "800", "900")
                        or (font_size_val and font_size_val >= 24)
                    )
                    else "medium"
                    if fw_str in ("500", "600", "medium")
                    else "regular"
                )

                text_content = "".join(elem.itertext()).strip()
                if text_content:
                    nid = generate_node_id("text", authored_id)
                    text_label = f"{label} ({anchor})" if anchor else label
                    nodes.append(
                        ModelCanvasTextNode.model_validate(
                            {
                                "id": nid,
                                "parentId": current_parent,
                                "label": text_label[:120],
                                "kind": "text",
                                "x": x,
                                "y": y,
                                "text": text_content[:240],
                                "fontSize": font_size_val,
                                "fontWeight": font_weight,
                                "textAnchor": anchor,
                                "style": "label",
                                "visualStyle": v_style.model_dump(by_alias=True)
                                if v_style
                                else None,
                                "hidden": is_hidden,
                            }
                        )
                    )
            except ValueError:
                pass

    for child in root:
        walk(child)

    if not nodes:
        raise ValueError("SVG contained no supported drawable vector elements")

    # 3. Process Educational Entities & Interactions
    compiled_entities: list[ModelCanvasEntity] = []
    compiled_interactions: list[ModelCanvasInteraction] = []
    node_id_set = {n.id for n in nodes}

    raw_entities = entities or []
    for ent in raw_entities:
        if isinstance(ent, dict):
            raw_eid = str(ent.get("id", ""))
            eid = _clean_node_id(raw_eid, "ent", len(compiled_entities) + 1)
            name = str(ent.get("name") or ent.get("label") or eid)
            desc = str(ent.get("description") or name)
            facts = [str(f) for f in ent.get("facts", [])]
            raw_nids = [str(n) for n in ent.get("nodeIds", [])]
            nids = [
                _clean_node_id(n, "node", 0)
                for n in raw_nids
                if _clean_node_id(n, "node", 0) in node_id_set
            ]
        elif isinstance(ent, ModelCanvasPlanEntity):
            eid = ent.id
            name = ent.label
            desc = ent.description or ent.label
            facts = ent.facts or ent.details
            nids = []
        else:
            eid = ent.id
            name = ent.name
            desc = ent.description
            facts = ent.facts
            nids = [n for n in ent.node_ids if n in node_id_set]

        # Auto-match if nodeIds is empty
        if not nids:
            if eid in node_id_set:
                nids = [eid]
            else:
                matching = [nid for nid in node_id_set if nid.startswith(eid)]
                nids = matching or [nodes[0].id]

        compiled_entities.append(
            ModelCanvasEntity.model_validate(
                {
                    "id": eid,
                    "name": name[:80],
                    "description": desc[:240],
                    "facts": facts[:4],
                    "nodeIds": nids[:16],
                }
            )
        )

        # Generate explore interaction binding for entity target
        trigger_id = nids[0]
        child_nids = [
            n.id for n in nodes if n.parent_id == trigger_id or n.id == trigger_id
        ]
        if not child_nids:
            child_nids = [trigger_id]
        hidden_child_nids = [
            nid for nid in child_nids if any(n.id == nid and n.hidden for n in nodes)
        ]
        compiled_interactions.append(
            ModelCanvasExploreInteraction.model_validate(
                {
                    "id": f"explore_{eid}",
                    "action": "explore",
                    "triggerNodeId": trigger_id,
                    "targetNodeId": trigger_id,
                    "revealNodeIds": hidden_child_nids[:16],
                    "animateNodeIds": child_nids[:16],
                    "exclusive": False,
                }
            )
        )

    # If no entities provided, generate default hotspots from prominent SVG IDs
    if not compiled_entities:
        for node in nodes[:4]:
            if node.id not in ("bg", "background", "title", "subtitle", "legend"):
                label = node.label or node.id.replace("_", " ").title()
                compiled_entities.append(
                    ModelCanvasEntity.model_validate(
                        {
                            "id": node.id,
                            "name": label[:80],
                            "description": f"Information about {label}.",
                            "facts": [],
                            "nodeIds": [node.id],
                        }
                    )
                )
                hidden_targets = [node.id] if node.hidden else []
                compiled_interactions.append(
                    ModelCanvasExploreInteraction.model_validate(
                        {
                            "id": f"explore_{node.id}",
                            "action": "explore",
                            "triggerNodeId": node.id,
                            "targetNodeId": node.id,
                            "revealNodeIds": hidden_targets,
                            "animateNodeIds": [node.id],
                            "exclusive": False,
                        }
                    )
                )

    compiled_motions: list[ModelCanvasMotion] = []
    if motions:
        for m in motions:
            if isinstance(m, dict):
                compiled_motions.append(ModelCanvasMotion.model_validate(m))
            else:
                compiled_motions.append(m)

    return ModelCanvasDraft.model_validate(
        {
            "sceneId": scene_id,
            "title": title[:100],
            "summary": summary[:320],
            "visualType": "freeform",
            "topology": "spatial",
            "nodes": [n.model_dump(by_alias=True) for n in nodes[:96]],
            "entities": [e.model_dump(by_alias=True) for e in compiled_entities[:16]],
            "interactions": [
                i.model_dump(by_alias=True) for i in compiled_interactions[:16]
            ],
            "motions": [m.model_dump(by_alias=True) for m in compiled_motions[:16]],
        }
    )
