from __future__ import annotations

from xml.sax.saxutils import escape

from ..canvas.protocol import (
    AvaCanvasTransaction,
    CanvasCircleNode,
    CanvasEllipseNode,
    CanvasGroupNode,
    CanvasLineNode,
    CanvasPathNode,
    CanvasPointsNode,
    CanvasRectNode,
    CanvasTextNode,
)


def export_transaction_to_svg(
    transaction: AvaCanvasTransaction,
    *,
    width: int = 1000,
    height: int = 600,
) -> str:
    """Export a validated AvaCanvasTransaction to a standalone interactive SVG."""
    defs: list[str] = [
        """    <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#0F172A" flood-opacity="0.35"/>
    </filter>""",
        """    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="8" result="blur"/>
      <feComposite in="SourceGraphic" in2="blur" operator="over"/>
    </filter>""",
        """    <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#4CC9F0" />
    </marker>""",
    ]

    elements: list[str] = []
    gradient_count = 0

    for op in transaction.operations:
        if op.operation != "create" or op.node is None:
            continue
        node = op.node

        # Resolve style
        is_stroke_only = getattr(node, "kind", "") in ("line", "arrow", "polyline") or (
            node.style
            and getattr(node.style, "stroke_color", None)
            and not getattr(node.style, "fill_color", None)
            and not getattr(node.style, "gradient", None)
            and not getattr(node.style, "radial_gradient", None)
        )
        fill = "none" if is_stroke_only else "#3B82F6"
        stroke = "none"
        stroke_width = "1"
        opacity = "1"
        filter_attr = ""

        if node.style:
            if node.style.fill_color:
                fill = node.style.fill_color
            elif getattr(node.style, "fill", "") in ("transparent", "none"):
                fill = "none"
            if node.style.stroke_color:
                stroke = node.style.stroke_color
            if node.style.stroke_width:
                stroke_width = str(node.style.stroke_width)
            if node.style.opacity is not None:
                opacity = str(node.style.opacity)
            if node.style.shadow:
                filter_attr = ' filter="url(#shadow)"'

            # Linear gradient
            if node.style.gradient:
                gradient_count += 1
                grad_id = f"grad_{node.id}_{gradient_count}"
                g = node.style.gradient
                stops_svg = "\n".join(
                    f'      <stop offset="{int(s.offset * 100)}%" stop-color="{s.color}"/>'
                    for s in g.stops
                )
                defs.append(
                    f'    <linearGradient id="{grad_id}" x1="{g.start.x}" y1="{g.start.y}" x2="{g.end.x}" y2="{g.end.y}">\n{stops_svg}\n    </linearGradient>'
                )
                fill = f"url(#{grad_id})"

            # Radial gradient
            if node.style.radial_gradient:
                gradient_count += 1
                grad_id = f"rgrad_{node.id}_{gradient_count}"
                rg = node.style.radial_gradient
                stops_svg = "\n".join(
                    f'      <stop offset="{int(s.offset * 100)}%" stop-color="{s.color}"/>'
                    for s in rg.stops
                )
                defs.append(
                    f'    <radialGradient id="{grad_id}" cx="{rg.center.x}" cy="{rg.center.y}" r="{rg.radius}" fx="{rg.center.x}" fy="{rg.center.y}" gradientUnits="userSpaceOnUse">\n{stops_svg}\n    </radialGradient>'
                )
                fill = f"url(#{grad_id})"

        # Hidden state
        style_extra = ""
        if not getattr(node, "visible", True) or getattr(node, "hidden", False):
            style_extra = ' display="none"'

        if isinstance(node, CanvasGroupNode):
            transform_str = ""
            if node.transform:
                tx = node.transform.translate_x
                ty = node.transform.translate_y
                rot = node.transform.rotation_degrees
                sc = node.transform.scale
                parts = []
                if tx != 0 or ty != 0:
                    parts.append(f"translate({tx}, {ty})")
                if rot != 0:
                    parts.append(f"rotate({rot})")
                if sc != 1:
                    parts.append(f"scale({sc})")
                if parts:
                    transform_str = f' transform="{" ".join(parts)}"'
            elements.append(
                f'  <g id="{node.id}" opacity="{opacity}"{transform_str}{style_extra}>'
            )
            elements.append("  </g>")

        elif isinstance(node, CanvasRectNode):
            elements.append(
                f'  <rect id="{node.id}" x="{node.x}" y="{node.y}" width="{node.width}" height="{node.height}" rx="{node.corner_radius}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"{filter_attr}{style_extra} />'
            )

        elif isinstance(node, CanvasCircleNode):
            elements.append(
                f'  <circle id="{node.id}" cx="{node.center.x}" cy="{node.center.y}" r="{node.radius}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"{filter_attr}{style_extra} />'
            )

        elif isinstance(node, CanvasEllipseNode):
            elements.append(
                f'  <ellipse id="{node.id}" cx="{node.center.x}" cy="{node.center.y}" rx="{node.radius_x}" ry="{node.radius_y}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"{filter_attr}{style_extra} />'
            )

        elif isinstance(node, CanvasLineNode):
            marker = ' marker-end="url(#arrowhead)"' if node.kind == "arrow" else ""
            elements.append(
                f'  <line id="{node.id}" x1="{node.start.x}" y1="{node.start.y}" x2="{node.end.x}" y2="{node.end.y}" stroke="{stroke if stroke != "none" else fill}" stroke-width="{stroke_width}" opacity="{opacity}"{marker}{style_extra} />'
            )

        elif isinstance(node, CanvasPathNode):
            elements.append(
                f'  <path id="{node.id}" d="{node.path_data}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"{filter_attr}{style_extra} />'
            )

        elif isinstance(node, CanvasPointsNode):
            pts_str = " ".join(f"{p.x},{p.y}" for p in node.points)
            tag = "polygon" if node.kind == "polygon" else "polyline"
            elements.append(
                f'  <{tag} id="{node.id}" points="{pts_str}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity}"{filter_attr}{style_extra} />'
            )

        elif isinstance(node, CanvasTextNode):
            font_size = node.font_size or 16
            font_weight = str(getattr(node, "font_weight", "500") or "500")
            text_anchor = str(getattr(node, "text_anchor", "start") or "start")
            fill_color = fill if fill != "#3B82F6" else "#0F172A"
            elements.append(
                f'  <text id="{node.id}" x="{node.x}" y="{node.y}" font-family="system-ui, -apple-system, sans-serif" font-size="{font_size}" font-weight="{font_weight}" fill="{fill_color}" text-anchor="{text_anchor}" opacity="{opacity}"{style_extra}>{escape(node.text)}</text>'
            )

    defs_svg = "\n".join(defs)
    content_svg = "\n".join(elements)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
{defs_svg}
  </defs>
  <!-- Background Canvas Card -->
  <rect width="{width}" height="{height}" rx="24" fill="#F8FAFC" />
{content_svg}
</svg>"""
