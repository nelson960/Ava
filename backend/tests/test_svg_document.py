from __future__ import annotations

import json
from hashlib import sha256

import pytest

from ava_backend.canvas.svg_document import (
    SVG_MANIFEST_VERSION,
    SVG_RENDERER_CAPABILITY_VERSION,
    SVG_VALIDATOR_VERSION,
    SvgDocumentValidationError,
    SvgDocumentValidator,
)


def _document(body: str, *, root_attributes: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" {root_attributes}>'
        "<title>Water cycle</title>"
        "<desc>How water moves between the sky and the ground.</desc>"
        f"{body}"
        "</svg>"
    )


def test_validator_preserves_exact_provider_unicode_bytes() -> None:
    svg = (
        "\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">\n'
        "  <title>Café water cycle ☁️</title>\n"
        "  <desc>Evaporation → clouds → rain.</desc>\n"
        '  <path id="river" d="M 10 500 C 200 450 700 550 990 490" '
        'fill="none" stroke="#2784c7"/>\n'
        "</svg>\n"
    )

    validated = SvgDocumentValidator().validate(svg)

    assert validated.svg_text == svg
    assert validated.svg_bytes == svg.encode("utf-8")
    assert validated.svg_sha256 == sha256(svg.encode("utf-8")).hexdigest()
    assert validated.title == "Café water cycle ☁️"
    assert validated.description == "Evaporation → clouds → rain."


def test_validator_builds_deterministic_content_bound_manifest() -> None:
    svg = _document(
        """
        <defs>
          <linearGradient id="water_gradient" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#b8ecff"/>
            <stop offset="1" stop-color="#1f72be"/>
          </linearGradient>
          <filter id="soft_shadow"><feDropShadow dx="0" dy="3" stdDeviation="4"/></filter>
        </defs>
        <g id="cloud" data-ava-binding="cloud_select" data-ava-action="select"
           data-ava-target="cloud" aria-label="Explore the rain cloud"
           fill="url(#water_gradient)" filter="url(#soft_shadow)">
          <ellipse cx="500" cy="180" rx="150" ry="65"/>
        </g>
        """,
        root_attributes='data-ava-background="#dff5ff"',
    )

    first = SvgDocumentValidator().validate(svg)
    second = SvgDocumentValidator().validate(svg)
    manifest = json.loads(first.manifest_bytes)

    assert first.manifest_bytes == second.manifest_bytes
    assert first.manifest_sha256 == sha256(first.manifest_bytes).hexdigest()
    assert manifest["manifestVersion"] == SVG_MANIFEST_VERSION
    assert manifest["rendererCapabilityVersion"] == SVG_RENDERER_CAPABILITY_VERSION
    assert manifest["validatorVersion"] == SVG_VALIDATOR_VERSION
    assert manifest["sourceTrust"] == "modelGeneratedData"
    assert manifest["bindings"] == [
        {
            "accessibilityLabel": "Explore the rain cloud",
            "action": "select",
            "bindingId": "cloud_select",
            "elementId": "cloud",
            "exclusive": False,
            "hitTest": "element",
            "pulseIds": [],
            "revealIds": [],
            "serverReportable": True,
            "targetId": "cloud",
        }
    ]


def test_validator_derives_only_bounded_visible_semantics_deterministically() -> None:
    svg = _document(
        """
        <defs><text id="template_label">Hidden template</text></defs>
        <g id="water" aria-label="Water in the lake">
          <text id="evaporation">Evaporation</text>
          <text id="hidden_label" display="none">Internal note</text>
        </g>
        <g id="cloud" data-ava-binding="select_cloud" data-ava-action="select"
           aria-label="Rain cloud"><circle cx="500" cy="150" r="50"/></g>
        """
    )

    first = SvgDocumentValidator().validate(svg)
    second = SvgDocumentValidator().validate(svg)

    assert first.visible_text_labels == ("Evaporation",)
    assert first.named_objects == ("Water in the lake", "Rain cloud")
    assert first.visible_text_labels == second.visible_text_labels
    assert first.named_objects == second.named_objects


@pytest.mark.parametrize(
    ("svg", "code"),
    [
        (
            '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            + _document("<text>&xxe;</text>"),
            "documentTypeForbidden",
        ),
        (_document("<script>alert(1)</script>"), "elementForbidden"),
        (_document("<foreignObject><p>html</p></foreignObject>"), "elementForbidden"),
        (_document('<rect onload="alert(1)"/>'), "eventHandlerForbidden"),
        (
            _document('<use href="https://example.com/a.svg#shape"/>'),
            "externalReferenceForbidden",
        ),
        (
            _document('<rect fill="url(https://example.com/a.svg#paint)"/>'),
            "externalReferenceForbidden",
        ),
        (_document("<style>rect { fill: red; }</style>"), "elementForbidden"),
        (
            _document('<rect style="cursor:url(https://example.com/cursor)"/>'),
            "stylePropertyForbidden",
        ),
        (_document('<image href="data:image/png;base64,AAAA"/>'), "elementForbidden"),
    ],
)
def test_validator_rejects_active_or_remote_content(svg: str, code: str) -> None:
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == code


def test_validator_rejects_missing_local_reference() -> None:
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(_document('<rect fill="url(#missing)"/>'))

    assert raised.value.code == "referenceMissing"


def test_validator_rejects_reference_cycles() -> None:
    svg = _document(
        """
        <defs>
          <g id="first"><use href="#second"/></g>
          <g id="second"><use href="#first"/></g>
        </defs>
        <use href="#first"/>
        """
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "referenceCycle"


def test_validator_requires_accessible_interaction_metadata() -> None:
    svg = _document(
        '<g id="cloud" data-ava-binding="cloud_select" data-ava-action="select"/>'
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "semanticTextMissing"


def test_validator_requires_binding_targets_to_exist() -> None:
    svg = _document(
        '<g id="cloud" data-ava-binding="cloud_reveal" data-ava-action="reveal" '
        'data-ava-reveal="rain" aria-label="Show rain"/>'
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "bindingTargetMissing"


def test_explore_manifest_declares_exclusive_reveals_and_connector_pulses() -> None:
    svg = _document(
        """
        <path id="connector" d="M100 100 L200 200"/>
        <g id="details" display="none"><rect x="200" y="200" width="100" height="50"/></g>
        <g id="cloud" data-ava-binding="explore_cloud" data-ava-action="explore"
          data-ava-target="cloud" data-ava-reveal="details"
          data-ava-pulse="connector" data-ava-exclusive="true"
          aria-label="Explore the cloud"><circle cx="100" cy="100" r="40"/></g>
        """
    )

    document = SvgDocumentValidator().validate(svg)
    binding = document.bindings["explore_cloud"]

    assert binding.exclusive is True
    assert binding.reveal_ids == ("details",)
    assert binding.pulse_ids == ("connector",)


def test_title_and_description_must_be_unique_direct_root_children() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600">'
        "<g><title>Nested</title></g><desc>Description</desc>"
        "</svg>"
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "semanticStructureInvalid"


def test_capability_manifest_is_versioned_and_bounded() -> None:
    manifest = SvgDocumentValidator().capability_manifest()

    assert manifest["rendererCapabilityVersion"] == SVG_RENDERER_CAPABILITY_VERSION
    assert manifest["validatorVersion"] == SVG_VALIDATOR_VERSION
    assert "script" not in manifest["allowedElements"]
    assert "viewBox" in manifest["allowedAttributes"]
    assert "foo" not in manifest["allowedAttributes"]
    assert manifest["allowedDataAttributePrefix"] == "data-ava-"
    assert manifest["limits"]["bytes"] > 0
