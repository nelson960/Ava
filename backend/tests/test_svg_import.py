from __future__ import annotations

from ava_backend.canvas.svg_import import parse_svg_to_canvas_draft
from ava_backend.realtime.model_plan import (
    ModelCanvasDraft,
    ModelCanvasPathNode,
    compile_model_canvas_draft,
)


def test_offline_svg_import_compiles_to_typed_transaction() -> None:
    draft = parse_svg_to_canvas_draft(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
          <path id="shape" d="M 100 100 L 300 100 L 200 260 Z" fill="#3B82F6"/>
          <text id="label" x="200" y="300">Synthetic label</text>
        </svg>""",
        scene_id="scene_import",
        title="Synthetic scene",
    )

    assert isinstance(draft, ModelCanvasDraft)
    assert isinstance(draft.nodes[0], ModelCanvasPathNode)

    transaction = compile_model_canvas_draft(
        draft,
        transaction_id="transaction_import",
    )
    assert transaction.scene_id == "scene_import"
    assert transaction.revision == 1
    assert transaction.operations


def test_offline_svg_import_excludes_script_nodes() -> None:
    draft = parse_svg_to_canvas_draft(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">
          <script>untrusted()</script>
          <circle id="safe" cx="500" cy="300" r="50" fill="#3B82F6"/>
        </svg>""",
        scene_id="scene_safe_import",
    )

    assert [node.id for node in draft.nodes] == ["safe"]
