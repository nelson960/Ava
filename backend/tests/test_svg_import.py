from __future__ import annotations

from ava_backend.canvas.svg_import import parse_svg_to_canvas_draft
from ava_backend.realtime.model_plan import (
    ModelCanvasCircleNode,
    ModelCanvasDraft,
    ModelCanvasEllipseNode,
    ModelCanvasGroupNode,
    ModelCanvasLineNode,
    ModelCanvasPathNode,
    ModelCanvasPolygonNode,
    ModelCanvasRectNode,
    ModelCanvasSpecialistDraft,
    ModelCanvasTextNode,
    compile_model_canvas_draft,
)


def test_parse_svg_with_paths_and_gradients() -> None:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
      <defs>
        <linearGradient id="skyGrad" x1="0" y1="0" x2="0" y2="600">
          <stop offset="0%" stop-color="#1E3A8A"/>
          <stop offset="100%" stop-color="#3B82F6"/>
        </linearGradient>
        <radialGradient id="sunGrad" cx="500" cy="200" r="100">
          <stop offset="0%" stop-color="#FBBF24"/>
          <stop offset="100%" stop-color="#D97706"/>
        </radialGradient>
        <filter id="softShadow">
          <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#0F172A" flood-opacity="0.35"/>
        </filter>
      </defs>
      <rect id="bg" x="40" y="40" width="920" height="520" fill="url(#skyGrad)" />
      <circle id="sun" cx="500" cy="200" r="60" fill="url(#sunGrad)" filter="url(#softShadow)" />
      <path id="mountain" d="M 200 480 Q 500 150 800 480 Z" fill="#4B5563" stroke="#1F2937" stroke-width="3" />
      <text id="title" x="500" y="80" fill="#FFFFFF">Mountain Landscape</text>
    </svg>"""

    entities = [
        {
            "id": "sun",
            "name": "The Sun",
            "description": "The star providing light and warmth.",
            "facts": ["Surface is 5,500°C", "Center of solar system"],
            "nodeIds": ["sun"],
        },
        {
            "id": "mountain",
            "name": "Mountain Peak",
            "description": "High rock elevation.",
            "facts": ["Formed by tectonic plates"],
            "nodeIds": ["mountain"],
        },
    ]

    draft = parse_svg_to_canvas_draft(
        svg,
        scene_id="scene_mountain",
        title="Mountain Landscape",
        summary="A scenic mountain peak with glowing sun and sky.",
        entities=entities,
    )

    assert isinstance(draft, ModelCanvasDraft)
    assert draft.scene_id == "scene_mountain"
    assert draft.title == "Mountain Landscape"
    assert len(draft.nodes) == 4
    assert len(draft.entities) == 2
    assert len(draft.interactions) == 2

    # Check node types
    assert isinstance(draft.nodes[0], ModelCanvasRectNode)
    assert draft.nodes[0].visual_style.gradient is not None
    assert isinstance(draft.nodes[1], ModelCanvasCircleNode)
    assert draft.nodes[1].visual_style.radial_gradient is not None
    assert draft.nodes[1].visual_style.shadow is not None
    assert isinstance(draft.nodes[2], ModelCanvasPathNode)
    assert draft.nodes[2].path_data == "M 200 480 Q 500 150 800 480 Z"
    assert isinstance(draft.nodes[3], ModelCanvasTextNode)
    assert draft.nodes[3].text == "Mountain Landscape"

    # Verify compilation to transaction
    tx = compile_model_canvas_draft(draft, transaction_id="tx_mountain")
    assert tx.scene_id == "scene_mountain"
    assert len(tx.operations) >= 4


def test_parse_svg_with_groups_and_polygons() -> None:
    svg = """<svg viewBox="0 0 1000 600">
      <g id="cell_group">
        <ellipse id="membrane" cx="500" cy="300" rx="200" ry="120" fill="#86EFAC" stroke="#15803D" stroke-width="4"/>
        <circle id="nucleus" cx="450" cy="280" r="40" fill="#C084FC"/>
        <line id="pointer" x1="450" y1="280" x2="350" y2="200" stroke="#000000" stroke-width="2"/>
        <polygon id="crystal" points="600,300 620,280 640,300 620,320" fill="#38BDF8"/>
      </g>
    </svg>"""

    draft = parse_svg_to_canvas_draft(
        svg,
        scene_id="scene_cell",
        title="Plant Cell",
    )

    assert len(draft.nodes) == 5
    assert isinstance(draft.nodes[0], ModelCanvasGroupNode)
    assert draft.nodes[0].id == "cell_group"

    # Children have parent_id set to cell_group
    for child in draft.nodes[1:]:
        assert child.parent_id == "cell_group"

    assert isinstance(draft.nodes[1], ModelCanvasEllipseNode)
    assert isinstance(draft.nodes[2], ModelCanvasCircleNode)
    assert isinstance(draft.nodes[3], ModelCanvasLineNode)
    assert isinstance(draft.nodes[4], ModelCanvasPolygonNode)


def test_specialist_draft_with_svg_field() -> None:
    plan = ModelCanvasSpecialistDraft.model_validate(
        {
            "title": "Volcano Eruption",
            "summary": "Magma rising through the conduit.",
            "svg": """<svg viewBox="0 0 1000 600">
              <path id="mountain" d="M 200 480 L 500 180 L 800 480 Z" fill="#78350F" />
              <ellipse id="chamber" cx="500" cy="480" rx="80" ry="30" fill="#EA580C" />
            </svg>""",
            "entities": [
                {
                    "id": "chamber",
                    "name": "Magma Chamber",
                    "description": "Underground magma pool.",
                    "facts": ["Extremely hot magma"],
                    "nodeIds": ["chamber"],
                }
            ],
        }
    )

    draft = plan.bind_scene_id("scene_volcano")
    assert isinstance(draft, ModelCanvasDraft)
    assert draft.scene_id == "scene_volcano"
    assert len(draft.nodes) == 2
    assert draft.nodes[0].id == "mountain"
    assert draft.nodes[1].id == "chamber"
    assert len(draft.entities) == 1
    assert len(draft.interactions) == 1


def test_parse_svg_strips_scripts_and_malformed_tags() -> None:
    svg = """<svg viewBox="0 0 1000 600">
      <script>alert('malicious')</script>
      <circle id="safe_circle" cx="500" cy="300" r="50" fill="#3B82F6" />
    </svg>"""

    draft = parse_svg_to_canvas_draft(
        svg,
        scene_id="scene_safe",
    )
    assert len(draft.nodes) == 1
    assert draft.nodes[0].id == "safe_circle"
