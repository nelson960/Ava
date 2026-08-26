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
from typing import Final, Mapping, NoReturn

SVG_NAMESPACE: Final = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE: Final = "http://www.w3.org/1999/xlink"
XML_NAMESPACE: Final = "http://www.w3.org/XML/1998/namespace"

SVG_VALIDATOR_VERSION: Final = "ava-svg-validator-v2"
SVG_RENDERER_CAPABILITY_VERSION: Final = "ava-svg-renderer-v2"
SVG_MANIFEST_VERSION: Final = "ava-svg-manifest-v2"

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,95}$")
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
    }
)
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


@dataclass(frozen=True, slots=True)
class SvgValidationLimits:
    max_bytes: int = 262_144
    max_elements: int = 384
    max_depth: int = 32
    max_attributes_per_element: int = 64
    max_total_attributes: int = 3_072
    max_attribute_characters: int = 131_072
    max_text_characters: int = 24_000
    max_title_characters: int = 160
    max_description_characters: int = 800
    max_path_characters: int = 96_000
    max_path_commands: int = 6_000
    max_transform_operations: int = 768
    max_references: int = 768
    max_reference_depth: int = 20
    max_expanded_elements: int = 2_048
    max_filter_primitives: int = 48
    max_bindings: int = 32
    max_viewbox_extent: float = 10_000.0


@dataclass(frozen=True, slots=True)
class SvgInteractionBinding:
    binding_id: str
    element_id: str
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


class SvgDocumentValidationError(ValueError):
    """The exact provider SVG is outside Ava's static renderer contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SvgDocumentValidator:
    """Validate exact SVG without rewriting, sanitizing, or reserializing it."""

    def __init__(self, limits: SvgValidationLimits | None = None) -> None:
        self._limits = limits or SvgValidationLimits()

    @property
    def limits(self) -> SvgValidationLimits:
        return self._limits

    def validate(self, svg_text: str) -> ValidatedSvgDocument:
        if not isinstance(svg_text, str):
            raise TypeError("svg_text must be a Unicode string")
        if _CONTROL_CHARACTER.search(svg_text):
            self._fail("forbiddenControlCharacter", "SVG contains a control character")
        svg_bytes = svg_text.encode("utf-8", errors="strict")
        if not svg_bytes:
            self._fail("emptyDocument", "SVG output is empty")
        if len(svg_bytes) > self._limits.max_bytes:
            self._fail("documentTooLarge", "SVG exceeds the byte limit")
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
        if interaction_mode_text is None:
            self._fail(
                "interactionModeInvalid",
                "SVG root requires a supported data-ava-interaction-mode",
            )
        try:
            interaction_mode = SvgInteractionMode(interaction_mode_text)
        except ValueError:
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
        if len(direct_titles) != 1 or len(direct_descriptions) != 1:
            self._fail(
                "semanticStructureInvalid",
                "SVG requires exactly one direct-child <title> and <desc>",
            )
        direct_semantic_elements = {
            id(direct_titles[0]),
            id(direct_descriptions[0]),
        }

        ids: set[str] = set()
        id_to_node: dict[str, ET.Element] = {}
        referenced_ids: set[str] = set()
        reference_graph: dict[str, set[str]] = defaultdict(set)
        bindings: dict[str, SvgInteractionBinding] = {}
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

        stack: list[tuple[ET.Element, int, str, bool]] = [
            (root, 1, "@root", True)
        ]
        while stack:
            element, depth, reference_owner, parent_is_rendered = stack.pop()
            total_elements += 1
            if total_elements > self._limits.max_elements:
                self._fail("tooManyElements", "SVG exceeds the element limit")
            if depth > self._limits.max_depth:
                self._fail("documentTooDeep", "SVG exceeds the XML depth limit")

            self._validate_element_namespace(element.tag)
            tag = _local_element_name(element.tag)
            if tag not in _ALLOWED_ELEMENTS:
                self._fail("elementForbidden", f"SVG element <{tag}> is not allowed")
            if tag in {"title", "desc"} and id(element) not in direct_semantic_elements:
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
                    )

            attributes = element.attrib
            element_is_rendered = (
                parent_is_rendered
                and tag not in _NON_RENDERED_SEMANTIC_CONTAINERS
                and _element_is_rendered(attributes)
            )
            if len(attributes) > self._limits.max_attributes_per_element:
                self._fail("tooManyAttributes", f"<{tag}> has too many attributes")
            total_attributes += len(attributes)
            if total_attributes > self._limits.max_total_attributes:
                self._fail("tooManyAttributes", "SVG exceeds the total attribute limit")

            element_id = attributes.get("id")
            if element_id is not None:
                self._validate_identifier(element_id, label="element id")
                if element_id in ids:
                    self._fail("duplicateId", f"SVG id {element_id!r} is duplicated")
                ids.add(element_id)
                id_to_node[element_id] = element
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
                        "attributeForbidden", f"SVG attribute {name!r} is not allowed"
                    )
                total_attribute_characters += len(name) + len(value)
                if total_attribute_characters > self._limits.max_attribute_characters:
                    self._fail(
                        "attributesTooLarge",
                        "SVG attributes exceed the character limit",
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
                self._fail("pathsTooLarge", "SVG path data exceeds the character limit")
            if total_path_commands > self._limits.max_path_commands:
                self._fail("tooManyPathCommands", "SVG exceeds the path command limit")
            if total_transform_operations > self._limits.max_transform_operations:
                self._fail(
                    "tooManyTransforms", "SVG exceeds the transform operation limit"
                )
            if total_references > self._limits.max_references:
                self._fail("tooManyReferences", "SVG exceeds the local reference limit")

            element_text = (element.text or "") + (element.tail or "")
            total_text_characters += len(element_text)
            if total_text_characters > self._limits.max_text_characters:
                self._fail("textTooLarge", "SVG text exceeds the character limit")
            if id(element) == id(direct_titles[0]):
                title = _semantic_text(element)
            elif id(element) == id(direct_descriptions[0]):
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
            if binding_id is not None:
                binding = self._binding_from_element(element, element_id)
                if binding.binding_id in bindings:
                    self._fail(
                        "duplicateBinding",
                        f"binding {binding.binding_id!r} is duplicated",
                    )
                bindings[binding.binding_id] = binding
                if len(bindings) > self._limits.max_bindings:
                    self._fail(
                        "tooManyBindings", "SVG exceeds the interaction binding limit"
                    )
            elif any(
                name in attributes
                for name in ("data-ava-target", "data-ava-reveal", "data-ava-step")
            ):
                self._fail(
                    "bindingMetadataWithoutBinding",
                    "SVG interaction metadata requires data-ava-binding",
                )

            children = list(element)
            for child_index, child in reversed(list(enumerate(children))):
                child_owner = element_id or f"{reference_owner}/{child_index}"
                stack.append(
                    (child, depth + 1, child_owner, element_is_rendered)
                )

        view_box = self._validate_view_box(root.attrib.get("viewBox"))
        title = self._validate_semantic_text(
            title,
            label="title",
            maximum=self._limits.max_title_characters,
        )
        description = self._validate_semantic_text(
            description,
            label="description",
            maximum=self._limits.max_description_characters,
        )
        missing = sorted(referenced_ids - ids)
        if missing:
            self._fail("referenceMissing", f"SVG references unknown id {missing[0]!r}")
        self._validate_reference_graph(reference_graph, id_to_node)
        self._validate_binding_targets(bindings, ids)
        self._validate_interaction_mode(interaction_mode, bindings)

        background = root.attrib.get("data-ava-background")
        if background is not None and len(background) > 96:
            self._fail("backgroundTooLarge", "data-ava-background exceeds its limit")
        manifest_dict: dict[str, object] = {
            "background": background,
            "bindings": [
                binding.as_json()
                for binding in sorted(
                    bindings.values(), key=lambda item: item.binding_id
                )
            ],
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
        )

    def capability_manifest(self) -> dict[str, object]:
        limits = self._limits
        return {
            "allowedAttributes": sorted(_ALLOWED_ATTRIBUTES),
            "allowedDataAttributes": sorted(_ALLOWED_DATA_ATTRIBUTES),
            "allowedElements": sorted(_ALLOWED_ELEMENTS),
            "allowedInteractionModes": [mode.value for mode in SvgInteractionMode],
            "allowedStyleProperties": sorted(_ALLOWED_STYLE_PROPERTIES),
            "limits": {
                "attributes": limits.max_total_attributes,
                "bindings": limits.max_bindings,
                "bytes": limits.max_bytes,
                "depth": limits.max_depth,
                "elements": limits.max_elements,
                "expandedElements": limits.max_expanded_elements,
                "filterPrimitives": limits.max_filter_primitives,
                "pathCommands": limits.max_path_commands,
                "referenceDepth": limits.max_reference_depth,
                "references": limits.max_references,
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
        binding_id = attributes["data-ava-binding"]
        self._validate_identifier(binding_id, label="binding id")
        if element_id is None:
            self._fail(
                "bindingElementMissingId",
                f"binding {binding_id!r} requires an element id",
            )
        accessibility_label = attributes.get("aria-label", "")
        accessibility_label = self._validate_semantic_text(
            accessibility_label,
            label=f"binding {binding_id!r} aria-label",
            maximum=160,
        )
        target_id = attributes.get("data-ava-target", element_id)
        self._validate_identifier(target_id, label="binding target id")
        reveal_text = attributes.get("data-ava-reveal", "")
        reveal_ids = tuple(filter(None, re.split(r"[\s,]+", reveal_text)))
        for reveal_id in reveal_ids:
            self._validate_identifier(reveal_id, label="binding reveal id")
        step_text = attributes.get("data-ava-step")
        step_index: int | None = None
        if step_text is not None:
            if not step_text.isascii() or not step_text.isdigit():
                self._fail(
                    "bindingStepInvalid",
                    f"binding {binding_id!r} data-ava-step must be an integer",
                )
            step_index = int(step_text)
            if not 1 <= step_index <= 12:
                self._fail(
                    "bindingStepInvalid",
                    f"binding {binding_id!r} data-ava-step is out of range",
                )
        return SvgInteractionBinding(
            binding_id=binding_id,
            element_id=element_id,
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
        if len(values) < 2 or len(values) > 12:
            self._fail(
                "interactionModeBindingsInvalid",
                "steps mode requires between 2 and 12 bindings",
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

    def _validate_element_namespace(self, raw_name: str) -> None:
        namespace = _namespace(raw_name)
        if namespace not in {None, SVG_NAMESPACE}:
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

    def _validate_identifier(self, value: str, *, label: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            self._fail("identifierInvalid", f"{label} {value!r} is invalid")

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
                "viewBoxInvalid", "SVG viewBox must contain exactly four finite numbers"
            )
        parsed = tuple(float(number) for number in numbers)
        if not all(math.isfinite(number) for number in parsed):
            self._fail("viewBoxInvalid", "SVG viewBox numbers must be finite")
        if parsed[2] <= 0 or parsed[3] <= 0:
            self._fail(
                "viewBoxInvalid", "SVG viewBox width and height must be positive"
            )
        if any(abs(number) > self._limits.max_viewbox_extent for number in parsed):
            self._fail("viewBoxInvalid", "SVG viewBox exceeds the renderer extent")
        return parsed  # type: ignore[return-value]

    def _validate_semantic_text(self, value: str, *, label: str, maximum: int) -> str:
        if not value or not value.strip():
            self._fail("semanticTextMissing", f"SVG {label} is required")
        if len(value) > maximum:
            self._fail(
                "semanticTextTooLarge", f"SVG {label} exceeds its character limit"
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
            missing = {
                binding.target_id,
                *binding.reveal_ids,
            } - ids
            if missing:
                self._fail(
                    "bindingTargetMissing",
                    f"binding {binding.binding_id!r} references unknown id {sorted(missing)[0]!r}",
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
                )

    @staticmethod
    def _fail(code: str, message: str) -> NoReturn:
        raise SvgDocumentValidationError(code, message)


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
            and _local_element_name(node.tag)
            not in _NON_RENDERED_SEMANTIC_CONTAINERS
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
