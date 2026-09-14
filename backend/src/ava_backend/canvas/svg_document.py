from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Final, Literal, Mapping, NoReturn

SVG_NAMESPACE: Final = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE: Final = "http://www.w3.org/1999/xlink"
XML_NAMESPACE: Final = "http://www.w3.org/XML/1998/namespace"

SVG_VALIDATOR_VERSION: Final = "ava-svg-validator-v2"
SVG_DRAFT_VALIDATOR_VERSION: Final = "ava-svg-draft-validator-v1"
SVG_RENDERER_CAPABILITY_VERSION: Final = "ava-svg-renderer-v2"
SVG_MANIFEST_VERSION: Final = "ava-svg-manifest-v2"
SVG_AUTHORING_CONTRACT_VERSION: Final = "ava-svg-authoring-contract-v1"
SVG_DEFAULT_MAX_BYTES: Final = 384 * 1024
SVG_PROTOCOL_MAX_BYTES: Final = 512 * 1024
SVG_MAX_MANIFEST_BYTES: Final = 64 * 1024

_IDENTIFIER_MAX_CHARACTERS: Final = 96
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_.:-]{0,95}$"
_IDENTIFIER = re.compile(_IDENTIFIER_PATTERN)
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")
_PATH_COMMAND = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]")
_TRANSFORM_FUNCTION = re.compile(r"[A-Za-z][A-Za-z0-9]*\s*\(")
_LOCAL_URL = re.compile(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.:-]{0,95})\s*\)", re.I)
_ANY_URL = re.compile(r"url\(", re.I)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_SEMANTIC_PROJECTION_ITEMS: Final = 32
_MAX_SEMANTIC_PROJECTION_ITEM_CHARACTERS: Final = 160
_NON_RENDERED_SEMANTIC_CONTAINERS: Final = frozenset(
    {
        "clipPath",
        "defs",
        "filter",
        "linearGradient",
        "marker",
        "mask",
        "pattern",
        "radialGradient",
    }
)

_ALLOWED_ELEMENTS: Final = frozenset(
    {
        "svg",
        "g",
        "defs",
        "title",
        "desc",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "filter",
        "feBlend",
        "feColorMatrix",
        "feComposite",
        "feDropShadow",
        "feFlood",
        "feGaussianBlur",
        "feOffset",
        "marker",
        "pattern",
        "use",
    }
)
_FILTER_PRIMITIVES: Final = frozenset(
    {
        "feBlend",
        "feColorMatrix",
        "feComposite",
        "feDropShadow",
        "feFlood",
        "feGaussianBlur",
        "feOffset",
    }
)
_ALLOWED_ATTRIBUTES: Final = frozenset(
    {
        "id",
        "class",
        "role",
        "aria-label",
        "viewBox",
        "preserveAspectRatio",
        "width",
        "height",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "fx",
        "fy",
        "fr",
        "d",
        "points",
        "pathLength",
        "transform",
        "gradientTransform",
        "patternTransform",
        "gradientUnits",
        "patternUnits",
        "patternContentUnits",
        "markerUnits",
        "markerWidth",
        "markerHeight",
        "refX",
        "refY",
        "orient",
        "offset",
        "stop-color",
        "stop-opacity",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "clip-path",
        "clip-rule",
        "mask",
        "filter",
        "marker-start",
        "marker-mid",
        "marker-end",
        "opacity",
        "display",
        "visibility",
        "color",
        "color-interpolation-filters",
        "vector-effect",
        "paint-order",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "letter-spacing",
        "word-spacing",
        "text-anchor",
        "dominant-baseline",
        "alignment-baseline",
        "baseline-shift",
        "textLength",
        "lengthAdjust",
        "style",
        "href",
        "space",
        "in",
        "in2",
        "result",
        "mode",
        "operator",
        "k1",
        "k2",
        "k3",
        "k4",
        "values",
        "type",
        "stdDeviation",
        "dx",
        "dy",
        "flood-color",
        "flood-opacity",
    }
)
_ALLOWED_DATA_ATTRIBUTES: Final = frozenset(
    {
        "data-ava-background",
        "data-ava-interaction-mode",
        "data-ava-binding",
        "data-ava-target",
        "data-ava-reveal",
        "data-ava-step",
        "data-ava-control",
        "data-ava-control-kind",
        "data-ava-track",
        "data-ava-checkpoints",
    }
)
_CONTROL_METADATA_ATTRIBUTES: Final = frozenset({"data-ava-control-kind", "data-ava-track", "data-ava-checkpoints"})
_ALLOWED_STYLE_PROPERTIES: Final = frozenset(
    {
        "alignment-baseline",
        "baseline-shift",
        "clip-path",
        "clip-rule",
        "color",
        "color-interpolation-filters",
        "display",
        "dominant-baseline",
        "fill",
        "fill-opacity",
        "fill-rule",
        "filter",
        "flood-color",
        "flood-opacity",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "letter-spacing",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "opacity",
        "paint-order",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "vector-effect",
        "visibility",
        "word-spacing",
    }
)
_REFERENCE_ATTRIBUTES: Final = frozenset(
    {
        "clip-path",
        "fill",
        "filter",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "stroke",
    }
)


class SvgInteractionMode(StrEnum):
    NONE = "none"
    EXPLORE = "explore"
    SELECT = "select"
    STEPS = "steps"


class SvgValidationCategory(StrEnum):
    ACCESSIBILITY = "accessibility"
    CAPABILITY = "capability"
    COMPLEXITY = "complexity"
    INTERACTION = "interaction"
    REFERENCE = "reference"
    SECURITY = "security"
    STRUCTURE = "structure"


@dataclass(frozen=True, slots=True)
class SvgValidationFeedback:
    category: SvgValidationCategory
    code: str
    message: str
    element_id: str | None = None
    element_name: str | None = None
    attribute: str | None = None
    observed_value: str | int | float | None = None
    observed_value_truncated: bool = False
    limit_name: str | None = None
    limit_value: str | int | float | None = None

    def as_json(self) -> dict[str, object]:
        values: dict[str, object | None] = {
            "affectedAttribute": self.attribute,
            "affectedElement": self.element_name,
            "affectedElementId": self.element_id,
            "category": self.category,
            "code": self.code,
            "limit": self.limit_value,
            "limitName": self.limit_name,
            "message": self.message,
            "observedValue": self.observed_value,
            "observedValueTruncated": (True if self.observed_value_truncated else None),
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class SvgValidationLimits:
    max_bytes: int = SVG_DEFAULT_MAX_BYTES
    max_manifest_bytes: int = SVG_MAX_MANIFEST_BYTES
    max_elements: int = 384
    max_depth: int = 32
    max_attributes_per_element: int = 64
    max_total_attributes: int = 3_072
    max_attribute_characters: int = 131_072
    max_text_characters: int = 24_000
    max_title_characters: int = 160
    max_description_characters: int = 800
    max_binding_label_characters: int = 160
    max_background_characters: int = 96
    max_path_characters: int = 96_000
    max_path_commands: int = 6_000
    max_transform_operations: int = 768
    max_references: int = 768
    max_reference_depth: int = 20
    max_expanded_elements: int = 2_048
    max_filter_primitives: int = 48
    max_bindings: int = 32
    max_step_bindings: int = 12
    max_viewbox_extent: float = 10_000.0


class SvgControlKind(StrEnum):
    DRAG = "drag"
    SLIDER = "slider"
    TIMELINE = "timeline"


@dataclass(frozen=True, slots=True)
class SvgControlCheckpoint:
    value: float
    binding_id: str

    def as_json(self) -> dict[str, object]:
        return {
            "bindingId": self.binding_id,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class SvgContinuousControl:
    control_id: str
    kind: SvgControlKind
    accessibility_label: str
    handle_element_id: str
    track_element_id: str
    initial_value: float
    checkpoints: tuple[SvgControlCheckpoint, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "accessibilityLabel": self.accessibility_label,
            "checkpoints": [checkpoint.as_json() for checkpoint in self.checkpoints],
            "controlId": self.control_id,
            "handleElementId": self.handle_element_id,
            "initialValue": self.initial_value,
            "kind": self.kind.value,
            "trackElementId": self.track_element_id,
        }


@dataclass(frozen=True, slots=True)
class SvgInteractionBinding:
    binding_id: str
    element_id: str
    element_name: str
    target_id: str
    accessibility_label: str
    reveal_ids: tuple[str, ...] = ()
    step_index: int | None = None

    def as_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "accessibilityLabel": self.accessibility_label,
            "bindingId": self.binding_id,
            "elementId": self.element_id,
            "revealIds": list(self.reveal_ids),
            "targetId": self.target_id,
        }
        if self.step_index is not None:
            value["stepIndex"] = self.step_index
        return value


@dataclass(frozen=True, slots=True)
class ValidatedSvgDocument:
    svg_text: str
    svg_bytes: bytes
    svg_sha256: str
    manifest: Mapping[str, object]
    manifest_bytes: bytes
    manifest_sha256: str
    title: str
    description: str
    interaction_mode: SvgInteractionMode
    visible_text_labels: tuple[str, ...]
    named_objects: tuple[str, ...]
    view_box: tuple[float, float, float, float]
    bindings: Mapping[str, SvgInteractionBinding]
    controls: Mapping[str, SvgContinuousControl]


def validate_svg_request_document_agreement(
    document: ValidatedSvgDocument,
    *,
    interaction_mode: str,
    continuous_control: str,
) -> None:
    if document.interaction_mode.value != interaction_mode:
        raise SvgDocumentValidationError(
            "interactionModeMismatch",
            "validated SVG interaction mode does not match the request",
            category=None,
            attribute="data-ava-interaction-mode",
            observed_value=document.interaction_mode.value,
            limit_name="requestedInteractionMode",
            limit_value=interaction_mode,
        )
    control_kinds = {control.kind.value for control in document.controls.values()}
    if continuous_control == "none":
        if document.controls:
            raise SvgDocumentValidationError(
                "continuousControlMismatch",
                "validated SVG contains controls when none were requested",
                category=None,
                attribute="data-ava-control-kind",
                observed_value=",".join(sorted(control_kinds)),
                limit_name="requestedContinuousControl",
                limit_value=continuous_control,
            )
        return
    if not document.controls:
        raise SvgDocumentValidationError(
            "continuousControlMissing",
            "validated SVG has no continuous control for the requested kind",
            category=None,
            attribute="data-ava-control-kind",
            observed_value="none",
            limit_name="requestedContinuousControl",
            limit_value=continuous_control,
        )
    if control_kinds != {continuous_control}:
        raise SvgDocumentValidationError(
            "continuousControlMismatch",
            "validated SVG continuous control kind does not match the request",
            category=None,
            attribute="data-ava-control-kind",
            observed_value=",".join(sorted(control_kinds)),
            limit_name="requestedContinuousControl",
            limit_value=continuous_control,
        )


def validate_svg_binding_continuity(
    base: ValidatedSvgDocument,
    revised: ValidatedSvgDocument,
) -> None:
    base_by_target: dict[str, set[str]] = defaultdict(set)
    revised_by_target: dict[str, set[str]] = defaultdict(set)
    for binding in base.bindings.values():
        base_by_target[binding.target_id].add(binding.binding_id)
    for binding in revised.bindings.values():
        revised_by_target[binding.target_id].add(binding.binding_id)
    for target_id in base_by_target.keys() & revised_by_target.keys():
        if base_by_target[target_id] != revised_by_target[target_id]:
            raise SvgDocumentValidationError(
                "bindingContinuityTargetMismatch",
                "a retained targetId must preserve its bindingId",
                category=None,
                attribute="data-ava-binding",
                observed_value=",".join(sorted(revised_by_target[target_id])),
                limit_name="previousBindingId",
                limit_value=",".join(sorted(base_by_target[target_id])),
            )
    for binding_id in base.bindings.keys() & revised.bindings.keys():
        base_target = base.bindings[binding_id].target_id
        revised_target = revised.bindings[binding_id].target_id
        if base_target != revised_target:
            raise SvgDocumentValidationError(
                "bindingContinuityReuseMismatch",
                "an existing bindingId cannot be reused for a different targetId",
                category=None,
                attribute="data-ava-target",
                observed_value=revised_target,
                limit_name="previousTargetId",
                limit_value=base_target,
            )


@dataclass(frozen=True, slots=True)
class SafeSvgDraftDocument:
    """Exact safe-to-mount bytes with no canonical or interaction authority."""

    svg_text: str
    svg_bytes: bytes
    svg_sha256: str
    view_box: tuple[float, float, float, float]
    validator_version: Literal["ava-svg-draft-validator-v1"] = (
        SVG_DRAFT_VALIDATOR_VERSION
    )
    authoritative: Literal[False] = False
    interactive: Literal[False] = False


class SvgDocumentValidationError(ValueError):
    """The exact provider SVG is outside Ava's static renderer contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: SvgValidationCategory | None = None,
        element_id: str | None = None,
        element_name: str | None = None,
        attribute: str | None = None,
        observed_value: str | int | float | None = None,
        limit_name: str | None = None,
        limit_value: str | int | float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        bounded_observed_value = observed_value
        observed_value_truncated = False
        if isinstance(observed_value, str) and len(observed_value) > 240:
            bounded_observed_value = observed_value[:240]
            observed_value_truncated = True
        self.feedback = SvgValidationFeedback(
            category=category or _validation_category(code),
            code=code,
            message=message,
            element_id=element_id,
            element_name=element_name,
            attribute=attribute,
            observed_value=bounded_observed_value,
            observed_value_truncated=observed_value_truncated,
            limit_name=limit_name,
            limit_value=limit_value,
        )


class SvgDocumentValidator:
    """Validate exact SVG without rewriting, sanitizing, or reserializing it."""

    def __init__(self, limits: SvgValidationLimits | None = None) -> None:
        self._limits = limits or SvgValidationLimits()

    @property
    def limits(self) -> SvgValidationLimits:
        return self._limits

    def validate(self, svg_text: str) -> ValidatedSvgDocument:
        document = self._validate_document(svg_text, require_final_authority=True)
        if not isinstance(document, ValidatedSvgDocument):  # pragma: no cover
            raise RuntimeError("final SVG validation returned a draft document")
        return document

    def validate_draft_safety(self, svg_text: str) -> SafeSvgDraftDocument:
        document = self._validate_document(svg_text, require_final_authority=False)
        if not isinstance(document, SafeSvgDraftDocument):  # pragma: no cover
            raise RuntimeError("draft SVG validation returned a canonical document")
        return document

    def _validate_document(
        self,
        svg_text: str,
        *,
        require_final_authority: bool,
    ) -> ValidatedSvgDocument | SafeSvgDraftDocument:
        if not isinstance(svg_text, str):
            raise TypeError("svg_text must be a Unicode string")
        if _CONTROL_CHARACTER.search(svg_text):
            self._fail("forbiddenControlCharacter", "SVG contains a control character")
        svg_bytes = svg_text.encode("utf-8", errors="strict")
        if not svg_bytes:
            self._fail("emptyDocument", "SVG output is empty")
        if len(svg_bytes) > self._limits.max_bytes:
            self._fail(
                "documentTooLarge",
                "SVG exceeds the byte limit",
                observed_value=len(svg_bytes),
                limit_name="maxBytes",
                limit_value=self._limits.max_bytes,
            )
        uppercase = svg_text.upper()
        if "<!DOCTYPE" in uppercase or "<!ENTITY" in uppercase:
            self._fail(
                "documentTypeForbidden", "SVG document types and entities are forbidden"
            )

        try:
            root = ET.fromstring(svg_text)
        except ET.ParseError as error:
            self._fail("invalidXml", f"SVG XML is invalid: {error}")
        if _local_element_name(root.tag) != "svg":
            self._fail("invalidRoot", "SVG root element must be <svg>")
        self._validate_element_namespace(root.tag)
        interaction_mode_text = root.attrib.get("data-ava-interaction-mode")
        interaction_mode: SvgInteractionMode | None = None
        if interaction_mode_text is None and require_final_authority:
            self._fail(
                "interactionModeInvalid",
                "SVG root requires a supported data-ava-interaction-mode",
            )
        if interaction_mode_text is not None:
            try:
                interaction_mode = SvgInteractionMode(interaction_mode_text)
            except ValueError:
                if require_final_authority:
                    self._fail(
                        "interactionModeInvalid",
                        "SVG root requires a supported data-ava-interaction-mode",
                    )
        direct_titles = [
            child for child in root if _local_element_name(child.tag) == "title"
        ]
        direct_descriptions = [
            child for child in root if _local_element_name(child.tag) == "desc"
        ]
        if require_final_authority and (
            len(direct_titles) != 1 or len(direct_descriptions) != 1
        ):
            self._fail(
                "semanticStructureInvalid",
                "SVG requires exactly one direct-child <title> and <desc>",
            )
        direct_title = direct_titles[0] if len(direct_titles) == 1 else None
        direct_description = (
            direct_descriptions[0] if len(direct_descriptions) == 1 else None
        )
        direct_semantic_elements = {
            id(element)
            for element in (direct_title, direct_description)
            if element is not None
        }

        ids: set[str] = set()
        id_to_node: dict[str, ET.Element] = {}
        referenced_ids: set[str] = set()
        reference_graph: dict[str, set[str]] = defaultdict(set)
        bindings: dict[str, SvgInteractionBinding] = {}
        controls: dict[str, SvgContinuousControl] = {}
        rendered_node_ids: set[str] = set()
        title = ""
        description = ""
        visible_text_labels: list[str] = []
        named_objects: list[str] = []
        total_elements = 0
        total_attributes = 0
        total_attribute_characters = 0
        total_text_characters = 0
        total_path_characters = 0
        total_path_commands = 0
        total_transform_operations = 0
        total_references = 0
        total_filter_primitives = 0

        stack: list[tuple[ET.Element, int, str, bool]] = [(root, 1, "@root", True)]
        while stack:
            element, depth, reference_owner, parent_is_rendered = stack.pop()
            total_elements += 1
            if total_elements > self._limits.max_elements:
                self._fail(
                    "tooManyElements",
                    "SVG exceeds the element limit",
                    observed_value=total_elements,
                    limit_name="maxElements",
                    limit_value=self._limits.max_elements,
                )
            if depth > self._limits.max_depth:
                self._fail(
                    "documentTooDeep",
                    "SVG exceeds the XML depth limit",
                    observed_value=depth,
                    limit_name="maxDepth",
                    limit_value=self._limits.max_depth,
                )

            self._validate_element_namespace(element.tag)
            tag = _local_element_name(element.tag)
            if tag not in _ALLOWED_ELEMENTS:
                self._fail(
                    "elementForbidden",
                    f"SVG element <{tag}> is not allowed",
                    element_id=element.attrib.get("id"),
                    element_name=tag,
                    observed_value=tag,
                    limit_name="allowedElements",
                )
            if (
                require_final_authority
                and tag in {"title", "desc"}
                and id(element) not in direct_semantic_elements
            ):
                self._fail(
                    "semanticStructureInvalid",
                    f"SVG <{tag}> must be a direct child of the root",
                )
            if tag in _FILTER_PRIMITIVES:
                total_filter_primitives += 1
                if total_filter_primitives > self._limits.max_filter_primitives:
                    self._fail(
                        "tooManyFilterPrimitives",
                        "SVG exceeds the filter primitive limit",
                        observed_value=total_filter_primitives,
                        limit_name="maxFilterPrimitives",
                        limit_value=self._limits.max_filter_primitives,
                    )

            attributes = element.attrib
            element_is_rendered = (
                parent_is_rendered
                and tag not in _NON_RENDERED_SEMANTIC_CONTAINERS
                and _element_is_rendered(attributes)
            )
            if len(attributes) > self._limits.max_attributes_per_element:
                self._fail(
                    "tooManyAttributes",
                    f"<{tag}> has too many attributes",
                    element_id=element.attrib.get("id"),
                    element_name=tag,
                    observed_value=len(attributes),
                    limit_name="maxAttributesPerElement",
                    limit_value=self._limits.max_attributes_per_element,
                )
            total_attributes += len(attributes)
            if total_attributes > self._limits.max_total_attributes:
                self._fail(
                    "tooManyAttributes",
                    "SVG exceeds the total attribute limit",
                    observed_value=total_attributes,
                    limit_name="maxTotalAttributes",
                    limit_value=self._limits.max_total_attributes,
                )

            element_id = attributes.get("id")
            if element_id is not None:
                self._validate_identifier(
                    element_id,
                    label="element id",
                    element_name=tag,
                    attribute="id",
                )
                if element_id in ids:
                    self._fail("duplicateId", f"SVG id {element_id!r} is duplicated")
                ids.add(element_id)
                id_to_node[element_id] = element
                if element_is_rendered:
                    rendered_node_ids.add(element_id)
            reference_source = element_id or reference_owner
            for raw_name, value in attributes.items():
                name = _local_attribute_name(raw_name)
                self._validate_attribute_namespace(raw_name)
                if name.lower().startswith("on"):
                    self._fail(
                        "eventHandlerForbidden",
                        f"event attribute {name!r} is forbidden",
                    )
                if not (
                    name in _ALLOWED_ATTRIBUTES or name in _ALLOWED_DATA_ATTRIBUTES
                ):
                    self._fail(
                        "attributeForbidden",
                        f"SVG attribute {name!r} is not allowed",
                        element_id=element_id,
                        element_name=tag,
                        attribute=name,
                        observed_value=value,
                        limit_name="allowedAttributes",
                    )
                total_attribute_characters += len(name) + len(value)
                if total_attribute_characters > self._limits.max_attribute_characters:
                    self._fail(
                        "attributesTooLarge",
                        "SVG attributes exceed the character limit",
                        observed_value=total_attribute_characters,
                        limit_name="maxAttributeCharacters",
                        limit_value=self._limits.max_attribute_characters,
                    )
                if _CONTROL_CHARACTER.search(value):
                    self._fail(
                        "forbiddenControlCharacter",
                        f"attribute {name!r} contains a control character",
                    )
                if name == "id":
                    continue
                elif name == "style":
                    refs = self._validate_style(value)
                    referenced_ids.update(refs)
                    reference_graph[reference_source].update(refs)
                    total_references += len(refs)
                elif name == "href":
                    reference = self._validate_fragment_reference(value, label="href")
                    referenced_ids.add(reference)
                    reference_graph[reference_source].add(reference)
                    total_references += 1
                elif name in _REFERENCE_ATTRIBUTES:
                    refs = self._validate_paint_reference(value, label=name)
                    referenced_ids.update(refs)
                    reference_graph[reference_source].update(refs)
                    total_references += len(refs)
                elif name == "d":
                    total_path_characters += len(value)
                    total_path_commands += len(_PATH_COMMAND.findall(value))
                elif name in {"transform", "gradientTransform", "patternTransform"}:
                    total_transform_operations += len(
                        _TRANSFORM_FUNCTION.findall(value)
                    )

            if total_path_characters > self._limits.max_path_characters:
                self._fail(
                    "pathsTooLarge",
                    "SVG path data exceeds the character limit",
                    observed_value=total_path_characters,
                    limit_name="maxPathCharacters",
                    limit_value=self._limits.max_path_characters,
                )
            if total_path_commands > self._limits.max_path_commands:
                self._fail(
                    "tooManyPathCommands",
                    "SVG exceeds the path command limit",
                    observed_value=total_path_commands,
                    limit_name="maxPathCommands",
                    limit_value=self._limits.max_path_commands,
                )
            if total_transform_operations > self._limits.max_transform_operations:
                self._fail(
                    "tooManyTransforms",
                    "SVG exceeds the transform operation limit",
                    observed_value=total_transform_operations,
                    limit_name="maxTransformOperations",
                    limit_value=self._limits.max_transform_operations,
                )
            if total_references > self._limits.max_references:
                self._fail(
                    "tooManyReferences",
                    "SVG exceeds the local reference limit",
                    observed_value=total_references,
                    limit_name="maxReferences",
                    limit_value=self._limits.max_references,
                )

            element_text = (element.text or "") + (element.tail or "")
            total_text_characters += len(element_text)
            if total_text_characters > self._limits.max_text_characters:
                self._fail(
                    "textTooLarge",
                    "SVG text exceeds the character limit",
                    observed_value=total_text_characters,
                    limit_name="maxTextCharacters",
                    limit_value=self._limits.max_text_characters,
                )
            if direct_title is not None and id(element) == id(direct_title):
                title = _semantic_text(element)
            elif direct_description is not None and id(element) == id(
                direct_description
            ):
                description = _semantic_text(element)
            elif tag == "text" and element_is_rendered:
                _append_semantic_projection_item(
                    visible_text_labels,
                    _visible_semantic_text(element, parent_is_rendered),
                )

            accessibility_label = attributes.get("aria-label")
            if element_is_rendered and accessibility_label is not None:
                _append_semantic_projection_item(
                    named_objects,
                    accessibility_label,
                )

            binding_id = attributes.get("data-ava-binding")
            if binding_id is not None and require_final_authority:
                binding = self._binding_from_element(element, element_id)
                if binding.binding_id in bindings:
                    self._fail(
                        "duplicateBinding",
                        f"binding {binding.binding_id!r} is duplicated",
                    )
                bindings[binding.binding_id] = binding
                if len(bindings) > self._limits.max_bindings:
                    self._fail(
                        "tooManyBindings",
                        "SVG exceeds the interaction binding limit",
                        observed_value=len(bindings),
                        limit_name="maxBindings",
                        limit_value=self._limits.max_bindings,
                    )
            elif require_final_authority and any(
                name in attributes
                for name in ("data-ava-target", "data-ava-reveal", "data-ava-step")
            ):
                self._fail(
                    "bindingMetadataWithoutBinding",
                    "SVG interaction metadata requires data-ava-binding",
                )

            control_id = attributes.get("data-ava-control")
            if control_id is not None and require_final_authority:
                control = self._control_from_element(element, element_id)
                if control.control_id in controls:
                    self._fail(
                        "duplicateControl",
                        f"control {control.control_id!r} is duplicated",
                    )
                controls[control.control_id] = control

            elif require_final_authority and any(
                name in attributes for name in _CONTROL_METADATA_ATTRIBUTES
            ):
                self._fail(
                    "controlMetadataWithoutControl",
                    "SVG continuous control metadata requires data-ava-control",
                )

            children = list(element)
            for child_index, child in reversed(list(enumerate(children))):
                child_owner = element_id or f"{reference_owner}/{child_index}"
                stack.append((child, depth + 1, child_owner, element_is_rendered))

        view_box = self._validate_view_box(root.attrib.get("viewBox"))
        if require_final_authority:
            title = self._validate_semantic_text(
                title,
                label="title",
                maximum=self._limits.max_title_characters,
                limit_name="maxTitleCharacters",
            )
            description = self._validate_semantic_text(
                description,
                label="description",
                maximum=self._limits.max_description_characters,
                limit_name="maxDescriptionCharacters",
            )
            missing = sorted(referenced_ids - ids)
            if missing:
                self._fail(
                    "referenceMissing",
                    f"SVG references unknown id {missing[0]!r}",
                    observed_value=missing[0],
                    limit_name="documentLocalReferences",
                )
        self._validate_reference_graph(reference_graph, id_to_node)
        if require_final_authority:
            self._validate_binding_targets(bindings, ids)
            if interaction_mode is None:  # pragma: no cover - checked above
                raise RuntimeError("final SVG lost its interaction mode")
            self._validate_interaction_mode(interaction_mode, bindings)
            self._validate_continuous_controls(controls, interaction_mode, bindings, ids, id_to_node, rendered_node_ids)

        background = root.attrib.get("data-ava-background")
        if (
            background is not None
            and len(background) > self._limits.max_background_characters
        ):
            self._fail(
                "backgroundTooLarge",
                "data-ava-background exceeds its limit",
                attribute="data-ava-background",
                observed_value=background,
                limit_name="maxBackgroundCharacters",
                limit_value=self._limits.max_background_characters,
            )
        if not require_final_authority:
            return SafeSvgDraftDocument(
                svg_text=svg_text,
                svg_bytes=svg_bytes,
                svg_sha256=sha256(svg_bytes).hexdigest(),
                view_box=view_box,
            )
        if interaction_mode is None:  # pragma: no cover - checked above
            raise RuntimeError("final SVG lost its interaction mode")
        manifest_dict: dict[str, object] = {
            "background": background,
            "bindings": [
                binding.as_json()
                for binding in sorted(
                    bindings.values(), key=lambda item: item.binding_id
                )
            ],
            **({"controls": [control.as_json() for control in sorted(controls.values(), key=lambda c: c.control_id)]} if controls else {}),
            "description": description,
            "interactionMode": interaction_mode,
            "manifestVersion": SVG_MANIFEST_VERSION,
            "rendererCapabilityVersion": SVG_RENDERER_CAPABILITY_VERSION,
            "sourceTrust": "modelGeneratedData",
            "title": title,
            "validatorVersion": SVG_VALIDATOR_VERSION,
            "viewBox": list(view_box),
        }
        manifest_bytes = json.dumps(
            manifest_dict,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(manifest_bytes) > self._limits.max_manifest_bytes:
            self._fail(
                "manifestTooLarge",
                "derived SVG manifest exceeds its byte limit",
                observed_value=len(manifest_bytes),
                limit_name="maxManifestBytes",
                limit_value=self._limits.max_manifest_bytes,
            )
        return ValidatedSvgDocument(
            svg_text=svg_text,
            svg_bytes=svg_bytes,
            svg_sha256=sha256(svg_bytes).hexdigest(),
            manifest=MappingProxyType(manifest_dict),
            manifest_bytes=manifest_bytes,
            manifest_sha256=sha256(manifest_bytes).hexdigest(),
            title=title,
            description=description,
            interaction_mode=interaction_mode,
            visible_text_labels=tuple(visible_text_labels),
            named_objects=tuple(named_objects),
            view_box=view_box,
            bindings=MappingProxyType(bindings.copy()),
            controls=MappingProxyType(controls.copy()),
        )

    def capability_manifest(self) -> dict[str, object]:
        limits = self._limits
        return {
            "allowedAttributes": sorted(_ALLOWED_ATTRIBUTES),
            "allowedDataAttributes": sorted(_ALLOWED_DATA_ATTRIBUTES),
            "allowedElements": sorted(_ALLOWED_ELEMENTS),
            "allowedFilterPrimitives": sorted(_FILTER_PRIMITIVES),
            "allowedInteractionModes": [mode.value for mode in SvgInteractionMode],
            "allowedStyleProperties": sorted(_ALLOWED_STYLE_PROPERTIES),
            "authoringContractVersion": SVG_AUTHORING_CONTRACT_VERSION,
            "documentContract": {
                "canonicalBytes": "exactUtf8ProviderOutput",
                "externalResources": False,
                "resolutionIndependentViewBox": True,
                "scripts": False,
            },
            "interactionEnvironment": {
                "clientExpandsCompactTargets": True,
                "defaultMinimumTargetPoints": 44,
                "manifestRequiredForAuthority": True,
            },
            "identifierContract": {
                "appliesTo": [
                    "bindingId",
                    "elementId",
                    "revealId",
                    "targetId",
                ],
                "maximumCharacters": _IDENTIFIER_MAX_CHARACTERS,
                "pattern": _IDENTIFIER_PATTERN,
            },
            "interactionContract": {
                "bindingElementRequiresId": True,
                "bindingCountByMode": {
                    "explore": {"maximum": limits.max_bindings, "minimum": 1},
                    "none": {"maximum": 0, "minimum": 0},
                    "select": {"maximum": limits.max_bindings, "minimum": 1},
                    "steps": {
                        "maximum": min(
                            limits.max_bindings,
                            limits.max_step_bindings,
                        ),
                        "minimum": 2,
                    },
                },
                "bindingId": {
                    "required": True,
                    "syntax": "identifierContract",
                    "unique": True,
                },
                "metadataRequiresBinding": [
                    "data-ava-reveal",
                    "data-ava-step",
                    "data-ava-target",
                ],
                "modeRules": {
                    "explore": {
                        "bindings": "required",
                        "reveals": "optionalDocumentLocalIds",
                        "stepIndexes": "forbidden",
                    },
                    "none": {
                        "bindings": "forbidden",
                        "reveals": "forbidden",
                        "stepIndexes": "forbidden",
                    },
                    "select": {
                        "bindings": "required",
                        "reveals": "forbidden",
                        "stepIndexes": "forbidden",
                    },
                    "steps": {
                        "bindings": "required",
                        "reveals": "forbidden",
                        "stepIndexes": "requiredUniqueContiguousFromOne",
                    },
                },
                "revealIds": {
                    "mustResolveToDocumentElementIds": True,
                    "syntax": "identifierContract",
                },
                "stepIndexes": "uniqueContiguousFromOne",
                "targetId": {
                    "explicitMustResolveToDocumentElementId": True,
                    "omittedDefaultsToBindingElement": True,
                    "syntax": "identifierContract",
                },
            },
            "controlsContract": {
                "allowedKinds": [kind.value for kind in SvgControlKind],
                "mode": "explore",
                "handle": "id + data-ava-control + data-ava-control-kind + aria-label",
                "track": "data-ava-track references a rendered path id",
                "checkpoints": "data-ava-checkpoints: value=bindingId;value=bindingId, normalized [0,1], distinct values and bindings",
                "initialValue": "first checkpoint in sorted normalized order",
                "bounds": "controls use the document element bound; checkpoints use the existing binding bound",
            },
            "referencePolicy": {
                "allowedNamespaces": [
                    "unqualified",
                    SVG_NAMESPACE,
                    XLINK_NAMESPACE,
                    XML_NAMESPACE,
                ],
                "documentLocalFragmentsOnly": True,
                "externalUrlsAllowed": False,
                "finalReferencesMustResolve": True,
                "referenceAttributes": sorted(_REFERENCE_ATTRIBUTES | {"href"}),
            },
            "limits": {
                "attributeCharacters": limits.max_attribute_characters,
                "attributesPerElement": limits.max_attributes_per_element,
                "attributes": limits.max_total_attributes,
                "backgroundCharacters": limits.max_background_characters,
                "bindingLabelCharacters": limits.max_binding_label_characters,
                "bindings": limits.max_bindings,
                "bytes": limits.max_bytes,
                "descriptionCharacters": limits.max_description_characters,
                "depth": limits.max_depth,
                "elements": limits.max_elements,
                "expandedElements": limits.max_expanded_elements,
                "filterPrimitives": limits.max_filter_primitives,
                "manifestBytes": limits.max_manifest_bytes,
                "pathCharacters": limits.max_path_characters,
                "pathCommands": limits.max_path_commands,
                "referenceDepth": limits.max_reference_depth,
                "references": limits.max_references,
                "textCharacters": limits.max_text_characters,
                "titleCharacters": limits.max_title_characters,
                "transformOperations": limits.max_transform_operations,
                "viewBoxExtent": limits.max_viewbox_extent,
            },
            "manifestVersion": SVG_MANIFEST_VERSION,
            "rendererCapabilityVersion": SVG_RENDERER_CAPABILITY_VERSION,
            "validatorVersion": SVG_VALIDATOR_VERSION,
        }

    def _binding_from_element(
        self,
        element: ET.Element,
        element_id: str | None,
    ) -> SvgInteractionBinding:
        attributes = element.attrib
        element_name = _local_element_name(element.tag)
        binding_id = attributes["data-ava-binding"]
        self._validate_identifier(
            binding_id,
            label="binding id",
            element_id=element_id,
            element_name=element_name,
            attribute="data-ava-binding",
        )
        if element_id is None:
            self._fail(
                "bindingElementMissingId",
                f"binding {binding_id!r} requires an element id",
                element_name=element_name,
                attribute="id",
            )
        accessibility_label = attributes.get("aria-label", "")
        accessibility_label = self._validate_semantic_text(
            accessibility_label,
            label=f"binding {binding_id!r} aria-label",
            maximum=self._limits.max_binding_label_characters,
            limit_name="maxBindingLabelCharacters",
        )
        target_id = attributes.get("data-ava-target", element_id)
        self._validate_identifier(
            target_id,
            label="binding target id",
            element_id=element_id,
            element_name=element_name,
            attribute="data-ava-target",
        )
        reveal_text = attributes.get("data-ava-reveal", "")
        reveal_ids = tuple(filter(None, re.split(r"[\s,]+", reveal_text)))
        for reveal_id in reveal_ids:
            self._validate_identifier(
                reveal_id,
                label="binding reveal id",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-reveal",
            )
        step_text = attributes.get("data-ava-step")
        step_index: int | None = None
        if step_text is not None:
            if not step_text.isascii() or not step_text.isdigit():
                self._fail(
                    "bindingStepInvalid",
                    f"binding {binding_id!r} data-ava-step must be an integer",
                )
            step_index = int(step_text)
            if not 1 <= step_index <= self._limits.max_step_bindings:
                self._fail(
                    "bindingStepInvalid",
                    f"binding {binding_id!r} data-ava-step is out of range",
                    element_id=element_id,
                    attribute="data-ava-step",
                    observed_value=step_index,
                    limit_name="maxStepBindings",
                    limit_value=self._limits.max_step_bindings,
                )
        return SvgInteractionBinding(
            binding_id=binding_id,
            element_id=element_id,
            element_name=element_name,
            target_id=target_id,
            accessibility_label=accessibility_label,
            reveal_ids=reveal_ids,
            step_index=step_index,
        )

    def _validate_interaction_mode(
        self,
        mode: SvgInteractionMode,
        bindings: Mapping[str, SvgInteractionBinding],
    ) -> None:
        values = tuple(bindings.values())
        if mode is SvgInteractionMode.NONE:
            if values:
                self._fail(
                    "interactionModeBindingsInvalid",
                    "interaction mode none cannot declare bindings",
                )
            return
        if not values:
            self._fail(
                "interactionModeBindingsInvalid",
                f"interaction mode {mode.value} requires bindings",
            )
        if mode is SvgInteractionMode.EXPLORE:
            if any(binding.step_index is not None for binding in values):
                self._fail(
                    "interactionModeBindingsInvalid",
                    "explore bindings cannot declare steps",
                )
            return
        if mode is SvgInteractionMode.SELECT:
            if any(
                binding.step_index is not None or binding.reveal_ids
                for binding in values
            ):
                self._fail(
                    "interactionModeBindingsInvalid",
                    "select bindings cannot declare steps or reveals",
                )
            return
        maximum_steps = min(
            self._limits.max_bindings,
            self._limits.max_step_bindings,
        )
        if len(values) < 2 or len(values) > maximum_steps:
            self._fail(
                "interactionModeBindingsInvalid",
                "steps mode requires a bounded ordered binding sequence",
                observed_value=len(values),
                limit_name="stepsBindingCount",
                limit_value=f"2..{maximum_steps}",
            )
        if any(binding.reveal_ids for binding in values):
            self._fail(
                "interactionModeBindingsInvalid",
                "steps bindings cannot declare reveals",
            )
        indexes: list[int] = []
        for binding in values:
            if binding.step_index is None:
                self._fail(
                    "interactionModeBindingsInvalid",
                    "steps bindings require unique contiguous indexes from 1",
                )
            indexes.append(binding.step_index)
        if sorted(indexes) != list(range(1, len(indexes) + 1)):
            self._fail(
                "interactionModeBindingsInvalid",
                "steps bindings require unique contiguous indexes from 1",
            )

    def _control_from_element(
        self,
        element: ET.Element,
        element_id: str | None,
    ) -> SvgContinuousControl:
        attributes = element.attrib
        element_name = _local_element_name(element.tag)
        control_id = attributes["data-ava-control"]
        self._validate_identifier(
            control_id,
            label="control id",
            element_id=element_id,
            element_name=element_name,
            attribute="data-ava-control",
        )
        if element_id is None:
            self._fail(
                "controlHandleMissingId",
                f"control {control_id!r} requires an element id on its handle element",
                element_name=element_name,
                attribute="id",
            )
        if "data-ava-binding" in attributes:
            self._fail(
                "controlHandleBindingConflict",
                f"element {element_id!r} cannot declare both data-ava-control and data-ava-binding",
                element_id=element_id,
                element_name=element_name,
            )
        raw_label = attributes.get("aria-label", "")
        accessibility_label = self._validate_semantic_text(
            raw_label,
            label=f"control {control_id!r} aria-label",
            maximum=self._limits.max_binding_label_characters,
            limit_name="maxControlLabelCharacters",
        )
        raw_kind = attributes.get("data-ava-control-kind")
        if raw_kind not in {k.value for k in SvgControlKind}:
            self._fail(
                "controlKindInvalid",
                f"control {control_id!r} kind {raw_kind!r} is invalid; must be one of {sorted(k.value for k in SvgControlKind)}",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-control-kind",
                observed_value=raw_kind,
            )
        kind = SvgControlKind(raw_kind)
        track_id = attributes.get("data-ava-track")
        if track_id is None:
            self._fail(
                "controlTrackMissing",
                f"control {control_id!r} requires data-ava-track",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-track",
                limit_name="controlsContract",
                limit_value="pathElementId",

            )
        self._validate_identifier(
            track_id,
            label="control track id",
            element_id=element_id,
            element_name=element_name,
            attribute="data-ava-track",
        )
        raw_checkpoints = attributes.get("data-ava-checkpoints")
        if not raw_checkpoints:
            self._fail(
                "controlCheckpointsInvalid",
                f"control {control_id!r} requires data-ava-checkpoints",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-checkpoints",
            )
        checkpoints = self._parse_checkpoints(
            raw_checkpoints,
            control_id=control_id,
            element_id=element_id,
            element_name=element_name,
        )
        return SvgContinuousControl(
            control_id=control_id,
            kind=kind,
            accessibility_label=accessibility_label,
            handle_element_id=element_id,
            track_element_id=track_id,
            initial_value=checkpoints[0].value,
            checkpoints=checkpoints,
        )

    def _parse_checkpoints(
        self,
        raw_checkpoints: str,
        *,
        control_id: str,
        element_id: str | None,
        element_name: str,
    ) -> tuple[SvgControlCheckpoint, ...]:
        items = raw_checkpoints.split(";")
        if len(items) < 2:
            self._fail(
                "controlCheckpointsCountInvalid",
                f"control {control_id!r} requires at least {2} checkpoints",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-checkpoints",
                observed_value=len(items),
                limit_name="minCheckpointsPerControl",
                limit_value=2,
            )
        if len(items) > self._limits.max_bindings:
            self._fail(
                "controlCheckpointsCountInvalid",
                f"control {control_id!r} exceeds checkpoint limit of {self._limits.max_bindings}",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-checkpoints",
                observed_value=len(items),
                limit_name="maxCheckpointsPerControl",
                limit_value=self._limits.max_bindings,
            )
        parsed: list[SvgControlCheckpoint] = []
        seen_values: list[float] = []
        for index, item in enumerate(items):
            if "=" not in item:
                self._fail(
                    "controlCheckpointsInvalid",
                    f"control {control_id!r} checkpoint {item!r} must follow value=bindingId syntax",
                    element_id=element_id,
                    element_name=element_name,
                    attribute="data-ava-checkpoints",
                    observed_value=item,
                )
            parts = item.split("=")
            if len(parts) != 2:
                self._fail(
                    "controlCheckpointsInvalid",
                    f"control {control_id!r} checkpoint {item!r} must contain exactly one '=' separator",
                    element_id=element_id,
                    element_name=element_name,
                    attribute="data-ava-checkpoints",
                    observed_value=item,
                )
            val_text, binding_ref = parts[0].strip(), parts[1].strip()
            if not val_text or not binding_ref:
                self._fail(
                    "controlCheckpointsInvalid",
                    f"control {control_id!r} checkpoint {item!r} has empty value or bindingId",
                    element_id=element_id,
                    element_name=element_name,
                    attribute="data-ava-checkpoints",
                    observed_value=item,
                )
            try:
                val = float(val_text)
            except ValueError:
                self._fail(
                    "controlCheckpointInvalid",
                    f"control {control_id!r} checkpoint value {val_text!r} is not a valid number",
                    element_id=element_id,
                    element_name=element_name,
                    attribute="data-ava-checkpoints",
                    observed_value=val_text,
                )
            if not math.isfinite(val) or val < 0.0 or val > 1.0:
                self._fail(
                    "controlCheckpointInvalid",
                    f"control {control_id!r} checkpoint value {val} is outside [0.0, 1.0]",
                    element_id=element_id,
                    element_name=element_name,
                    attribute="data-ava-checkpoints",
                    observed_value=val,
                )
            self._validate_identifier(
                binding_ref,
                label=f"control {control_id!r} checkpoint[{index}] bindingId",
                element_id=element_id,
                element_name=element_name,
                attribute="data-ava-checkpoints",
            )
            if any(
                val == existing for existing in seen_values
            ):
                self._fail(
                    "controlCheckpointDuplicateValue",
                    f"control {control_id!r} contains duplicate checkpoint value {val}",
                    element_id=element_id,
                    element_name=element_name,
                    attribute="data-ava-checkpoints",
                    observed_value=val,
                )
            seen_values.append(val)
            parsed.append(SvgControlCheckpoint(value=val, binding_id=binding_ref))
        if len({cp.binding_id for cp in parsed}) != len(parsed):
            self._fail("controlCheckpointDuplicateBinding", "control checkpoints must reference distinct bindings")
        return tuple(sorted(parsed, key=lambda cp: cp.value))

    def _validate_continuous_controls(
        self,
        controls: Mapping[str, SvgContinuousControl],
        mode: SvgInteractionMode,
        bindings: Mapping[str, SvgInteractionBinding],
        ids: set[str],
        id_to_node: Mapping[str, ET.Element],
        rendered_node_ids: set[str],
    ) -> None:
        if not controls:
            return
        if mode != SvgInteractionMode.EXPLORE:
            self._fail(
                "interactionModeControlsForbidden",
                f"continuous controls are forbidden in {mode.value} mode; only explore mode supports continuous controls",
                observed_value=mode.value,
            )
        for control in controls.values():
            if control.handle_element_id not in ids:
                self._fail(
                    "controlHandleMissing",
                    f"control {control.control_id!r} handle {control.handle_element_id!r} is missing from document",
                    element_id=control.handle_element_id,
                )
            if control.handle_element_id not in rendered_node_ids:
                self._fail(
                    "controlHandleNotRendered",
                    f"control {control.control_id!r} handle {control.handle_element_id!r} must be in the rendered document hierarchy",
                    element_id=control.handle_element_id,
                )
            if control.track_element_id not in ids:
                self._fail(
                    "controlTrackMissing",
                    f"control {control.control_id!r} references unknown track {control.track_element_id!r}",
                    element_id=control.handle_element_id,
                    attribute="data-ava-track",
                    observed_value=control.track_element_id,
                    limit_name="controlsContract",
                    limit_value="pathElementId",

                )
            track_node = id_to_node[control.track_element_id]
            if _local_element_name(track_node.tag) != "path":
                self._fail(
                    "controlTrackInvalid",
                    f"control {control.control_id!r} track {control.track_element_id!r} must be an SVG <path> element, found <{_local_element_name(track_node.tag)}>",
                    element_id=control.handle_element_id,
                    element_name=_local_element_name(track_node.tag),
                    attribute="data-ava-track",
                    observed_value=_local_element_name(track_node.tag),
                    limit_name="controlsContract",
                    limit_value="path",

                )
            track_d = (track_node.attrib.get("d") or "").strip()
            if not track_d or not _PATH_COMMAND.search(track_d):
                self._fail(
                    "controlTrackInvalid",
                    f"control {control.control_id!r} track {control.track_element_id!r} path data 'd' is missing or has no path commands",
                    element_id=control.track_element_id,
                    attribute="d",
                    observed_value=track_d,
                    limit_name="controlsContract",
                    limit_value="valid path data d",

                )
            for cp in control.checkpoints:
                if cp.binding_id not in bindings:
                    self._fail(
                        "controlCheckpointUnknownBinding",
                        f"control {control.control_id!r} checkpoint references unknown binding {cp.binding_id!r}",
                        observed_value=cp.binding_id,
                    )

    def _validate_element_namespace(self, raw_name: str) -> None:
        namespace = _namespace(raw_name)
        if namespace != SVG_NAMESPACE:
            self._fail(
                "namespaceForbidden",
                f"SVG element namespace {namespace!r} is forbidden",
            )

    def _validate_attribute_namespace(self, raw_name: str) -> None:
        namespace = _namespace(raw_name)
        if namespace not in {None, XLINK_NAMESPACE, XML_NAMESPACE}:
            self._fail(
                "namespaceForbidden",
                f"SVG attribute namespace {namespace!r} is forbidden",
            )
        if namespace == XML_NAMESPACE and _local_attribute_name(raw_name) != "space":
            self._fail(
                "attributeForbidden",
                f"XML attribute {_local_attribute_name(raw_name)!r} is forbidden",
            )

    def _validate_identifier(
        self,
        value: str,
        *,
        label: str,
        element_id: str | None = None,
        element_name: str | None = None,
        attribute: str | None = None,
    ) -> None:
        if not _IDENTIFIER.fullmatch(value):
            self._fail(
                "identifierInvalid",
                f"{label} {value!r} is invalid",
                element_id=element_id,
                element_name=element_name,
                attribute=attribute,
                observed_value=value,
                limit_name="identifierContract",
                limit_value=f"{_IDENTIFIER_MAX_CHARACTERS}:{_IDENTIFIER_PATTERN}",
            )

    def _validate_style(self, style: str) -> set[str]:
        if any(token in style for token in ("{", "}", "@", "/*", "*/")):
            self._fail("styleInvalid", "SVG style contains forbidden CSS syntax")
        references: set[str] = set()
        for declaration in style.split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration:
                self._fail("styleInvalid", "SVG style declaration is invalid")
            name, value = declaration.split(":", 1)
            property_name = name.strip().lower()
            property_value = value.strip()
            if property_name not in _ALLOWED_STYLE_PROPERTIES:
                self._fail(
                    "stylePropertyForbidden",
                    f"CSS property {property_name!r} is forbidden",
                )
            references.update(
                self._validate_paint_reference(property_value, label=property_name)
            )
        return references

    def _validate_fragment_reference(self, value: str, *, label: str) -> str:
        if not value.startswith("#") or not _IDENTIFIER.fullmatch(value[1:]):
            self._fail(
                "externalReferenceForbidden",
                f"{label} must be a local fragment reference",
            )
        return value[1:]

    def _validate_paint_reference(self, value: str, *, label: str) -> set[str]:
        if not _ANY_URL.search(value):
            return set()
        matches = list(_LOCAL_URL.finditer(value))
        without_local_urls = _LOCAL_URL.sub("", value)
        if _ANY_URL.search(without_local_urls):
            self._fail(
                "externalReferenceForbidden", f"{label} contains a non-local URL"
            )
        return {match.group(1) for match in matches}

    def _validate_view_box(
        self, value: str | None
    ) -> tuple[float, float, float, float]:
        if value is None:
            self._fail("viewBoxMissing", "SVG root requires viewBox")
        numbers = _NUMBER.findall(value)
        remainder = _NUMBER.sub("", value).replace(",", "").strip()
        if len(numbers) != 4 or remainder:
            self._fail(
                "viewBoxInvalid",
                "SVG viewBox must contain exactly four finite numbers",
                attribute="viewBox",
                observed_value=value,
                limit_name="viewBoxComponents",
                limit_value=4,
            )
        parsed = tuple(float(number) for number in numbers)
        if not all(math.isfinite(number) for number in parsed):
            self._fail(
                "viewBoxInvalid",
                "SVG viewBox numbers must be finite",
                attribute="viewBox",
                observed_value=value,
            )
        if parsed[2] <= 0 or parsed[3] <= 0:
            self._fail(
                "viewBoxInvalid",
                "SVG viewBox width and height must be positive",
                attribute="viewBox",
                observed_value=value,
            )
        if any(abs(number) > self._limits.max_viewbox_extent for number in parsed):
            self._fail(
                "viewBoxInvalid",
                "SVG viewBox exceeds the renderer extent",
                attribute="viewBox",
                observed_value=value,
                limit_name="maxViewBoxExtent",
                limit_value=self._limits.max_viewbox_extent,
            )
        return parsed  # type: ignore[return-value]

    def _validate_semantic_text(
        self,
        value: str,
        *,
        label: str,
        maximum: int,
        limit_name: str,
    ) -> str:
        if not value or not value.strip():
            self._fail("semanticTextMissing", f"SVG {label} is required")
        if len(value) > maximum:
            self._fail(
                "semanticTextTooLarge",
                f"SVG {label} exceeds its character limit",
                observed_value=len(value),
                limit_name=limit_name,
                limit_value=maximum,
            )
        if _CONTROL_CHARACTER.search(value):
            self._fail(
                "forbiddenControlCharacter", f"SVG {label} contains a control character"
            )
        return value

    def _validate_binding_targets(
        self,
        bindings: Mapping[str, SvgInteractionBinding],
        ids: set[str],
    ) -> None:
        for binding in bindings.values():
            if binding.target_id not in ids:
                self._fail(
                    "bindingTargetMissing",
                    f"binding {binding.binding_id!r} references unknown id {binding.target_id!r}",
                    element_id=binding.element_id,
                    element_name=binding.element_name,
                    attribute="data-ava-target",
                    observed_value=binding.target_id,
                    limit_name="documentLocalBindingReferences",
                )
            for reveal_id in binding.reveal_ids:
                if reveal_id in ids:
                    continue
                self._fail(
                    "bindingTargetMissing",
                    f"binding {binding.binding_id!r} references unknown id {reveal_id!r}",
                    element_id=binding.element_id,
                    element_name=binding.element_name,
                    attribute="data-ava-reveal",
                    observed_value=reveal_id,
                    limit_name="documentLocalBindingReferences",
                )

    def _validate_reference_graph(
        self,
        graph: Mapping[str, set[str]],
        id_to_node: Mapping[str, ET.Element],
    ) -> None:
        state: dict[str, int] = {}
        memo: dict[str, int] = {}

        def visit(node: str, depth: int) -> int:
            if depth > self._limits.max_reference_depth:
                self._fail(
                    "referenceGraphTooDeep",
                    "SVG reference graph exceeds its depth limit",
                    observed_value=depth,
                    limit_name="maxReferenceDepth",
                    limit_value=self._limits.max_reference_depth,
                )
            if state.get(node) == 1:
                self._fail("referenceCycle", f"SVG reference cycle includes {node!r}")
            if state.get(node) == 2:
                return memo[node]
            state[node] = 1
            expanded = 1
            for target in sorted(graph.get(node, ())):
                expanded += visit(target, depth + 1)
                if expanded > self._limits.max_expanded_elements:
                    self._fail(
                        "referenceExpansionTooLarge",
                        "SVG reference expansion exceeds its limit",
                        observed_value=expanded,
                        limit_name="maxExpandedElements",
                        limit_value=self._limits.max_expanded_elements,
                    )
            state[node] = 2
            memo[node] = expanded
            return expanded

        expanded_total = 0
        for node in sorted({*graph, *id_to_node}):
            expanded_total += visit(node, 1)
            if expanded_total > self._limits.max_expanded_elements:
                self._fail(
                    "referenceExpansionTooLarge",
                    "SVG reference expansion exceeds its limit",
                    observed_value=expanded_total,
                    limit_name="maxExpandedElements",
                    limit_value=self._limits.max_expanded_elements,
                )

    @staticmethod
    def _fail(
        code: str,
        message: str,
        *,
        element_id: str | None = None,
        element_name: str | None = None,
        attribute: str | None = None,
        observed_value: str | int | float | None = None,
        limit_name: str | None = None,
        limit_value: str | int | float | None = None,
    ) -> NoReturn:
        raise SvgDocumentValidationError(
            code,
            message,
            element_id=element_id,
            element_name=element_name,
            attribute=attribute,
            observed_value=observed_value,
            limit_name=limit_name,
            limit_value=limit_value,
        )


class SvgDraftSafetyValidator:
    """Validate exact draft bytes for mounting without granting final authority."""

    def __init__(self, document_validator: SvgDocumentValidator) -> None:
        self._document_validator = document_validator

    @property
    def limits(self) -> SvgValidationLimits:
        return self._document_validator.limits

    def validate(self, svg_text: str) -> SafeSvgDraftDocument:
        return self._document_validator.validate_draft_safety(svg_text)


def _validation_category(code: str) -> SvgValidationCategory:
    if code in {
        "documentTypeForbidden",
        "eventHandlerForbidden",
        "externalReferenceForbidden",
        "forbiddenControlCharacter",
        "namespaceForbidden",
    }:
        return SvgValidationCategory.SECURITY
    if (
        code.startswith("interaction")
        or code.startswith("binding")
        or code == "duplicateBinding"
    ):
        return SvgValidationCategory.INTERACTION
    if "Reference" in code or code.startswith("reference"):
        return SvgValidationCategory.REFERENCE
    if code.startswith("semantic"):
        return SvgValidationCategory.ACCESSIBILITY
    if (
        code.startswith("tooMany")
        or code.endswith("TooLarge")
        or code
        in {
            "documentTooDeep",
            "documentTooLarge",
            "pathsTooLarge",
        }
    ):
        return SvgValidationCategory.COMPLEXITY
    if code in {
        "attributeForbidden",
        "elementForbidden",
        "styleInvalid",
        "stylePropertyForbidden",
        "viewBoxInvalid",
        "viewBoxMissing",
    }:
        return SvgValidationCategory.CAPABILITY
    return SvgValidationCategory.STRUCTURE


def _namespace(raw_name: str) -> str | None:
    if not raw_name.startswith("{"):
        return None
    return raw_name[1:].split("}", 1)[0]


def _local_element_name(raw_name: str) -> str:
    return raw_name.rsplit("}", 1)[-1]


def _local_attribute_name(raw_name: str) -> str:
    return raw_name.rsplit("}", 1)[-1]


def _semantic_text(element: ET.Element) -> str:
    return "".join(element.itertext())


def _append_semantic_projection_item(values: list[str], value: str) -> None:
    normalized = " ".join(value.split())
    if not normalized or normalized in values:
        return
    if len(values) >= _MAX_SEMANTIC_PROJECTION_ITEMS:
        return
    values.append(normalized[:_MAX_SEMANTIC_PROJECTION_ITEM_CHARACTERS].rstrip())


def _visible_semantic_text(
    element: ET.Element,
    parent_is_rendered: bool,
) -> str:
    values: list[str] = []

    def visit(node: ET.Element, inherited_is_rendered: bool) -> None:
        rendered = (
            inherited_is_rendered
            and _local_element_name(node.tag) not in _NON_RENDERED_SEMANTIC_CONTAINERS
            and _element_is_rendered(node.attrib)
        )
        if rendered and node.text:
            values.append(node.text)
        for child in node:
            visit(child, rendered)
            if rendered and child.tail:
                values.append(child.tail)

    visit(element, parent_is_rendered)
    return "".join(values)


def _element_is_rendered(attributes: Mapping[str, str]) -> bool:
    presentation: dict[str, str] = {}
    for name in ("display", "visibility", "opacity"):
        value = attributes.get(name)
        if value is not None:
            presentation[name] = value.strip().lower()
    style = attributes.get("style")
    if style is not None:
        for declaration in style.split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            property_name = name.strip().lower()
            if property_name in {"display", "visibility", "opacity"}:
                presentation[property_name] = value.strip().lower()
    if presentation.get("display") == "none":
        return False
    if presentation.get("visibility") in {"collapse", "hidden"}:
        return False
    opacity = presentation.get("opacity")
    if opacity is not None:
        try:
            if float(opacity) == 0:
                return False
        except ValueError:
            # The validator reports malformed presentation values through its
            # existing attribute/style validation path.
            pass
    return True
