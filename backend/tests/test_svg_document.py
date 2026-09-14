from __future__ import annotations

import json
from hashlib import sha256

import pytest

from ava_backend.canvas.svg_document import (
    SVG_MANIFEST_VERSION,
    SVG_RENDERER_CAPABILITY_VERSION,
    SVG_VALIDATOR_VERSION,
    SafeSvgDraftDocument,
    SvgDocumentValidationError,
    SvgDocumentValidator,
    SvgDraftSafetyValidator,
    SvgValidationLimits,
    validate_svg_binding_continuity,
)


def _document(body: str, *, root_attributes: str = "") -> str:
    interaction_mode = (
        ""
        if "data-ava-interaction-mode" in root_attributes
        else 'data-ava-interaction-mode="none"'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        f"{interaction_mode} {root_attributes}>"
        "<title>Water cycle</title>"
        "<desc>How water moves between the sky and the ground.</desc>"
        f"{body}"
        "</svg>"
    )


def _binding_document(*bindings: tuple[str, str], label: str = "Target"):
    body = "".join(
        f'<g id="{target_id}" data-ava-binding="{binding_id}" '
        f'data-ava-target="{target_id}" aria-label="{label} {target_id}"/>'
        for target_id, binding_id in bindings
    )
    return SvgDocumentValidator().validate(
        _document(body, root_attributes='data-ava-interaction-mode="select"')
    )


def test_revise_binding_continuity_accepts_stable_target_and_binding() -> None:
    base = _binding_document(("target-a", "binding-a"), label="Old")
    revised = _binding_document(("target-a", "binding-a"), label="Improved")

    validate_svg_binding_continuity(base, revised)


def test_revise_binding_continuity_rejects_changed_binding_for_retained_target() -> (
    None
):
    base = _binding_document(("target-a", "binding-a"))
    revised = _binding_document(("target-a", "binding-renamed"))

    with pytest.raises(SvgDocumentValidationError) as raised:
        validate_svg_binding_continuity(base, revised)

    assert raised.value.code == "bindingContinuityTargetMismatch"


def test_revise_binding_continuity_rejects_reused_binding_for_different_target() -> (
    None
):
    base = _binding_document(("target-a", "binding-a"))
    revised = _binding_document(("target-b", "binding-a"))

    with pytest.raises(SvgDocumentValidationError) as raised:
        validate_svg_binding_continuity(base, revised)

    assert raised.value.code == "bindingContinuityReuseMismatch"


def test_revise_binding_continuity_allows_removed_target() -> None:
    base = _binding_document(
        ("target-a", "binding-a"),
        ("target-b", "binding-b"),
    )
    revised = _binding_document(("target-a", "binding-a"))

    validate_svg_binding_continuity(base, revised)


def test_revise_binding_continuity_allows_new_target() -> None:
    base = _binding_document(("target-a", "binding-a"))
    revised = _binding_document(
        ("target-a", "binding-a"),
        ("target-b", "binding-b"),
    )

    validate_svg_binding_continuity(base, revised)


def test_validator_preserves_exact_provider_unicode_bytes() -> None:
    svg = (
        "\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">\n'
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
        <g id="cloud" data-ava-binding="cloud_select"
           data-ava-target="cloud" aria-label="Explore the rain cloud"
           fill="url(#water_gradient)" filter="url(#soft_shadow)">
          <ellipse cx="500" cy="180" rx="150" ry="65"/>
        </g>
        """,
        root_attributes=(
            'data-ava-background="#dff5ff" data-ava-interaction-mode="select"'
        ),
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
            "bindingId": "cloud_select",
            "elementId": "cloud",
            "revealIds": [],
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
        <g id="cloud" data-ava-binding="select_cloud"
           aria-label="Rain cloud"><circle cx="500" cy="150" r="50"/></g>
        """,
        root_attributes='data-ava-interaction-mode="select"',
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


def test_validation_feedback_is_structured_bounded_and_deterministic() -> None:
    invalid = _document(
        f'<rect id="bad_rect" unsupported="{"x" * 400}" width="20" height="20"/>'
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(invalid)

    feedback = raised.value.feedback.as_json()
    assert feedback == {
        "affectedAttribute": "unsupported",
        "affectedElement": "rect",
        "affectedElementId": "bad_rect",
        "category": "capability",
        "code": "attributeForbidden",
        "limitName": "allowedAttributes",
        "message": "SVG attribute 'unsupported' is not allowed",
        "observedValue": "x" * 240,
        "observedValueTruncated": True,
    }


def test_validation_limit_feedback_identifies_observed_and_allowed_bytes() -> None:
    svg = _document('<rect width="20" height="20"/>')

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator(SvgValidationLimits(max_bytes=40)).validate(svg)

    feedback = raised.value.feedback.as_json()
    assert feedback["category"] == "complexity"
    assert feedback["code"] == "documentTooLarge"
    assert feedback["observedValue"] == len(svg.encode("utf-8"))
    assert feedback["limitName"] == "maxBytes"
    assert feedback["limit"] == 40


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
        '<g id="cloud" data-ava-binding="cloud_select"/>',
        root_attributes='data-ava-interaction-mode="select"',
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "semanticTextMissing"


def test_validator_requires_binding_targets_to_exist() -> None:
    svg = _document(
        '<g id="cloud" data-ava-binding="cloud_reveal" '
        'data-ava-reveal="rain" aria-label="Show rain"/>',
        root_attributes='data-ava-interaction-mode="explore"',
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "bindingTargetMissing"


def test_draft_safety_accepts_incomplete_binding_without_final_authority() -> None:
    svg = _document(
        '<ellipse id="cloud" data-ava-binding="cloud_reveal" '
        'data-ava-reveal="rain" aria-label="Show rain" '
        'cx="500" cy="200" rx="120" ry="60"/>',
        root_attributes='data-ava-interaction-mode="explore"',
    )
    validator = SvgDocumentValidator()

    draft = SvgDraftSafetyValidator(validator).validate(svg)

    assert isinstance(draft, SafeSvgDraftDocument)
    assert draft.svg_text == svg
    assert draft.svg_bytes == svg.encode("utf-8")
    assert draft.authoritative is False
    assert draft.interactive is False
    assert not hasattr(draft, "manifest")
    assert not hasattr(draft, "bindings")
    with pytest.raises(SvgDocumentValidationError) as raised:
        validator.validate(svg)
    assert raised.value.code == "bindingTargetMissing"


def test_draft_safety_still_rejects_unsafe_mountable_content() -> None:
    unsafe = _document('<script id="payload">alert(1)</script>')

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDraftSafetyValidator(SvgDocumentValidator()).validate(unsafe)

    assert raised.value.code == "elementForbidden"


def test_explore_manifest_declares_reveals() -> None:
    svg = _document(
        """
        <g id="details" display="none"><rect x="200" y="200" width="100" height="50"/></g>
        <g id="cloud" data-ava-binding="explore_cloud"
          data-ava-target="cloud" data-ava-reveal="details"
          aria-label="Explore the cloud"><circle cx="100" cy="100" r="40"/></g>
        """,
        root_attributes='data-ava-interaction-mode="explore"',
    )

    document = SvgDocumentValidator().validate(svg)
    binding = document.bindings["explore_cloud"]

    assert binding.reveal_ids == ("details",)


def test_title_and_description_must_be_unique_direct_root_children() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
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
    assert manifest["allowedDataAttributes"] == [
        "data-ava-background",
        "data-ava-binding",
        "data-ava-checkpoints",
        "data-ava-control",
        "data-ava-control-kind",
        "data-ava-interaction-mode",
        "data-ava-reveal",
        "data-ava-step",
        "data-ava-target",
        "data-ava-track",
    ]
    assert manifest["limits"]["bytes"] > 0
    assert manifest["limits"]["bytes"] == 384 * 1024
    assert manifest["documentContract"]["resolutionIndependentViewBox"] is True
    assert manifest["documentContract"]["canonicalBytes"] == ("exactUtf8ProviderOutput")
    assert manifest["interactionEnvironment"]["manifestRequiredForAuthority"] is True
    assert manifest["interactionContract"]["bindingCountByMode"]["steps"] == {
        "maximum": 12,
        "minimum": 2,
    }
    assert manifest["identifierContract"] == {
        "appliesTo": ["bindingId", "elementId", "revealId", "targetId"],
        "maximumCharacters": 96,
        "pattern": r"^[A-Za-z_][A-Za-z0-9_.:-]{0,95}$",
    }
    interaction = manifest["interactionContract"]
    assert interaction["bindingElementRequiresId"] is True
    assert interaction["bindingId"]["syntax"] == "identifierContract"
    assert interaction["targetId"] == {
        "explicitMustResolveToDocumentElementId": True,
        "omittedDefaultsToBindingElement": True,
        "syntax": "identifierContract",
    }
    assert interaction["revealIds"]["mustResolveToDocumentElementIds"] is True
    assert interaction["metadataRequiresBinding"] == [
        "data-ava-reveal",
        "data-ava-step",
        "data-ava-target",
    ]
    assert interaction["modeRules"]["steps"]["stepIndexes"] == (
        "requiredUniqueContiguousFromOne"
    )
    assert manifest["referencePolicy"]["documentLocalFragmentsOnly"] is True
    assert manifest["referencePolicy"]["finalReferencesMustResolve"] is True
    assert manifest["limits"]["manifestBytes"] == 64 * 1024
    assert manifest["limits"]["titleCharacters"] == 160
    assert manifest["limits"]["descriptionCharacters"] == 800


def test_none_document_rejects_bindings() -> None:
    svg = _document(
        '<g id="cloud" data-ava-binding="cloud" aria-label="Cloud"/>',
        root_attributes='data-ava-interaction-mode="none"',
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "interactionModeBindingsInvalid"


def test_explore_document_builds_typed_binding_without_action() -> None:
    svg = _document(
        '<g id="detail" display="none"><text>Cloud detail</text></g>'
        '<g id="cloud" data-ava-binding="cloud" data-ava-target="cloud" '
        'data-ava-reveal="detail" aria-label="Cloud"/>',
        root_attributes='data-ava-interaction-mode="explore"',
    )

    document = SvgDocumentValidator().validate(svg)

    assert document.interaction_mode == "explore"
    assert document.bindings["cloud"].reveal_ids == ("detail",)
    assert "action" not in document.manifest["bindings"][0]


def test_unknown_interaction_mode_is_rejected() -> None:
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(
            _document("", root_attributes='data-ava-interaction-mode="quiz"')
        )

    assert raised.value.code == "interactionModeInvalid"


def test_static_document_has_exact_empty_binding_manifest() -> None:
    document = SvgDocumentValidator().validate(
        _document(
            '<text id="label" x="20" y="40">Static label</text>',
            root_attributes='data-ava-interaction-mode="none"',
        )
    )

    assert document.interaction_mode == "none"
    assert document.bindings == {}
    assert document.manifest["interactionMode"] == "none"
    assert document.manifest["bindings"] == []


def test_select_document_rejects_reveal_metadata() -> None:
    svg = _document(
        '<g id="detail"/><g id="cloud" data-ava-binding="cloud" '
        'data-ava-reveal="detail" aria-label="Cloud"/>',
        root_attributes='data-ava-interaction-mode="select"',
    )

    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "interactionModeBindingsInvalid"


def test_steps_require_unique_contiguous_indexes() -> None:
    valid = _document(
        '<g id="egg" data-ava-binding="egg" data-ava-step="1" '
        'aria-label="Egg stage"/>'
        '<g id="larva" data-ava-binding="larva" data-ava-step="2" '
        'aria-label="Larva stage"/>',
        root_attributes='data-ava-interaction-mode="steps"',
    )
    document = SvgDocumentValidator().validate(valid)
    assert [binding.step_index for binding in document.bindings.values()] == [1, 2]

    for body in (
        '<g id="egg" data-ava-binding="egg" data-ava-step="1" aria-label="Egg stage"/>',
        '<g id="egg" data-ava-binding="egg" data-ava-step="1" '
        'aria-label="Egg stage"/><g id="larva" data-ava-binding="larva" '
        'data-ava-step="3" aria-label="Larva stage"/>',
        '<g id="egg" data-ava-binding="egg" data-ava-step="1" '
        'aria-label="Egg stage"/><g id="larva" data-ava-binding="larva" '
        'data-ava-step="1" aria-label="Larva stage"/>',
    ):
        with pytest.raises(SvgDocumentValidationError) as raised:
            SvgDocumentValidator().validate(
                _document(
                    body,
                    root_attributes='data-ava-interaction-mode="steps"',
                )
            )
        assert raised.value.code == "interactionModeBindingsInvalid"


def test_explore_document_rejects_step_metadata() -> None:
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(
            _document(
                '<g id="cloud" data-ava-binding="cloud" data-ava-step="1" '
                'aria-label="Cloud"/>',
                root_attributes='data-ava-interaction-mode="explore"',
            )
        )

    assert raised.value.code == "interactionModeBindingsInvalid"


@pytest.mark.parametrize(
    "attribute",
    [
        'data-ava-action="select"',
        'data-ava-pulse="connector"',
        'data-ava-exclusive="true"',
        'data-ava-value="cloud"',
    ],
)
def test_legacy_generic_interaction_attributes_are_rejected(attribute: str) -> None:
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(
            _document(
                f'<g id="cloud" {attribute} aria-label="Cloud"/>',
                root_attributes='data-ava-interaction-mode="none"',
            )
        )

    assert raised.value.code == "attributeForbidden"


def test_validator_rejects_svg_root_missing_svg_namespace() -> None:
    svg = (
        '<svg viewBox="0 0 1000 600" data-ava-interaction-mode="none">'
        "<title>Water cycle</title>"
        "<desc>How water moves between the sky and the ground.</desc>"
        '<circle id="c1" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "namespaceForbidden"
    assert raised.value.feedback.category.value == "security"


def test_validator_accepts_svg_with_valid_svg_namespace() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
        "<title>Water cycle</title>"
        "<desc>How water moves between the sky and the ground.</desc>"
        '<circle id="c1" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    validated = SvgDocumentValidator().validate(svg)
    assert validated.svg_text == svg


def test_validator_rejects_descendant_resetting_to_null_namespace() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
        "<title>Water cycle</title>"
        "<desc>How water moves between the sky and the ground.</desc>"
        '<g xmlns=""><circle id="c1" cx="50" cy="50" r="20"/></g>'
        "</svg>"
    )
    with pytest.raises(SvgDocumentValidationError) as raised:
        SvgDocumentValidator().validate(svg)

    assert raised.value.code == "namespaceForbidden"


def test_draft_safety_validator_enforces_svg_namespace() -> None:
    validator = SvgDocumentValidator()
    safety_validator = SvgDraftSafetyValidator(validator)

    invalid_draft = (
        '<svg viewBox="0 0 1000 600" data-ava-interaction-mode="none">'
        '<circle id="c1" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    with pytest.raises(SvgDocumentValidationError) as raised:
        safety_validator.validate(invalid_draft)
    assert raised.value.code == "namespaceForbidden"

    valid_draft = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 600" '
        'data-ava-interaction-mode="none">'
        '<circle id="c1" cx="50" cy="50" r="20"/>'
        "</svg>"
    )
    draft = safety_validator.validate(valid_draft)
    assert isinstance(draft, SafeSvgDraftDocument)
    assert draft.svg_text == valid_draft
