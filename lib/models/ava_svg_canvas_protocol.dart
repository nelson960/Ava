import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

const avaSvgRendererCapabilityVersion = 'ava-svg-renderer-v2';
const avaSvgValidatorVersion = 'ava-svg-validator-v2';
const avaSvgManifestVersion = 'ava-svg-manifest-v2';
const avaSvgMaximumDocumentBytes = 262144;
const avaSvgMaximumManifestBytes = 65536;

final RegExp _identifier = RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,95}$');
final RegExp _sha256 = RegExp(r'^[a-f0-9]{64}$');

enum AvaSvgInteractionMode { none, explore, select, steps }

final class AvaSvgInteractionBinding {
  const AvaSvgInteractionBinding({
    required this.bindingId,
    required this.elementId,
    required this.targetId,
    required this.accessibilityLabel,
    required this.revealIds,
    this.stepIndex,
  });

  final String bindingId;
  final String elementId;
  final String targetId;
  final String accessibilityLabel;
  final List<String> revealIds;
  final int? stepIndex;

  factory AvaSvgInteractionBinding.fromJson(Map<String, Object?> json) {
    final expected = <String>{
      'accessibilityLabel',
      'bindingId',
      'elementId',
      'revealIds',
      'targetId',
      if (json.containsKey('stepIndex')) 'stepIndex',
    };
    _requireExactKeys(json, expected, 'SVG interaction binding');
    final bindingId = _requiredIdentifier(
      json,
      'bindingId',
      'SVG interaction binding',
    );
    final elementId = _requiredIdentifier(
      json,
      'elementId',
      'SVG interaction binding',
    );
    final targetId = _requiredIdentifier(
      json,
      'targetId',
      'SVG interaction binding',
    );
    final label = _boundedString(
      json,
      'accessibilityLabel',
      'SVG interaction binding',
      160,
    );
    final rawRevealIds = json['revealIds'];
    if (rawRevealIds is! List || rawRevealIds.length > 32) {
      throw const FormatException(
        'SVG interaction binding.revealIds must be a bounded list.',
      );
    }
    final revealIds = rawRevealIds.map((value) {
      if (value is! String || !_identifier.hasMatch(value)) {
        throw const FormatException(
          'SVG interaction binding.revealIds contains an invalid ID.',
        );
      }
      return value;
    }).toList(growable: false);
    final stepIndex = json['stepIndex'];
    if (stepIndex != null &&
        (stepIndex is! int || stepIndex < 1 || stepIndex > 12)) {
      throw const FormatException(
        'SVG interaction binding.stepIndex is invalid.',
      );
    }
    return AvaSvgInteractionBinding(
      bindingId: bindingId,
      elementId: elementId,
      targetId: targetId,
      accessibilityLabel: label,
      revealIds: List.unmodifiable(revealIds),
      stepIndex: stepIndex as int?,
    );
  }
}

final class AvaSvgCanvasManifest {
  AvaSvgCanvasManifest({
    required this.title,
    required this.description,
    required this.background,
    required this.viewBox,
    required this.interactionMode,
    required Map<String, AvaSvgInteractionBinding> bindings,
  }) : bindings = Map.unmodifiable(bindings);

  final String title;
  final String description;
  final String? background;
  final List<double> viewBox;
  final AvaSvgInteractionMode interactionMode;
  final Map<String, AvaSvgInteractionBinding> bindings;

  factory AvaSvgCanvasManifest.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'background',
        'bindings',
        'description',
        'interactionMode',
        'manifestVersion',
        'rendererCapabilityVersion',
        'sourceTrust',
        'title',
        'validatorVersion',
        'viewBox',
      },
      'SVG manifest',
    );
    if (json['manifestVersion'] != avaSvgManifestVersion ||
        json['rendererCapabilityVersion'] != avaSvgRendererCapabilityVersion ||
        json['validatorVersion'] != avaSvgValidatorVersion ||
        json['sourceTrust'] != 'modelGeneratedData') {
      throw const FormatException(
        'SVG manifest version or trust boundary is unsupported.',
      );
    }
    final title = _boundedString(json, 'title', 'SVG manifest', 160);
    final description =
        _boundedString(json, 'description', 'SVG manifest', 800);
    final background = json['background'];
    if (background != null &&
        (background is! String || background.length > 96)) {
      throw const FormatException('SVG manifest.background is invalid.');
    }
    final rawViewBox = json['viewBox'];
    if (rawViewBox is! List || rawViewBox.length != 4) {
      throw const FormatException(
        'SVG manifest.viewBox must contain four numbers.',
      );
    }
    final viewBox = rawViewBox.map((value) {
      if (value is! num || !value.isFinite) {
        throw const FormatException(
          'SVG manifest.viewBox values must be finite.',
        );
      }
      return value.toDouble();
    }).toList(growable: false);
    if (viewBox[2] <= 0 || viewBox[3] <= 0) {
      throw const FormatException(
        'SVG manifest.viewBox width and height must be positive.',
      );
    }
    final rawBindings = json['bindings'];
    if (rawBindings is! List || rawBindings.length > 32) {
      throw const FormatException(
        'SVG manifest.bindings must be a bounded list.',
      );
    }
    final bindings = <String, AvaSvgInteractionBinding>{};
    for (final value in rawBindings) {
      if (value is! Map) {
        throw const FormatException(
          'SVG manifest.bindings must contain JSON objects.',
        );
      }
      final binding = AvaSvgInteractionBinding.fromJson(
        value.cast<String, Object?>(),
      );
      if (bindings.putIfAbsent(binding.bindingId, () => binding) != binding) {
        throw const FormatException('SVG manifest binding ID is duplicated.');
      }
    }
    final modeName = _requiredString(json, 'interactionMode', 'SVG manifest');
    final interactionMode = AvaSvgInteractionMode.values
        .where((candidate) => candidate.name == modeName)
        .firstOrNull;
    if (interactionMode == null) {
      throw const FormatException(
          'SVG manifest.interactionMode is unsupported.');
    }
    _validateInteractionMode(interactionMode, bindings.values);
    return AvaSvgCanvasManifest(
      title: title,
      description: description,
      background: background as String?,
      viewBox: List.unmodifiable(viewBox),
      interactionMode: interactionMode,
      bindings: bindings,
    );
  }
}

void _validateInteractionMode(
  AvaSvgInteractionMode mode,
  Iterable<AvaSvgInteractionBinding> bindings,
) {
  final values = bindings.toList(growable: false);
  switch (mode) {
    case AvaSvgInteractionMode.none:
      if (values.isNotEmpty) {
        throw const FormatException(
            'Static SVG manifest cannot contain bindings.');
      }
      return;
    case AvaSvgInteractionMode.explore:
      if (values.isEmpty ||
          values.any((binding) => binding.stepIndex != null)) {
        throw const FormatException(
            'Explore SVG manifest bindings are invalid.');
      }
      return;
    case AvaSvgInteractionMode.select:
      if (values.isEmpty ||
          values.any(
            (binding) =>
                binding.stepIndex != null || binding.revealIds.isNotEmpty,
          )) {
        throw const FormatException(
            'Select SVG manifest bindings are invalid.');
      }
      return;
    case AvaSvgInteractionMode.steps:
      final indexes = values.map((binding) => binding.stepIndex).toList()
        ..sort(
          (left, right) => (left ?? 0).compareTo(right ?? 0),
        );
      if (values.length < 2 ||
          values.length > 12 ||
          values.any((binding) => binding.revealIds.isNotEmpty) ||
          indexes.asMap().entries.any(
                (entry) => entry.value != entry.key + 1,
              )) {
        throw const FormatException('Steps SVG manifest bindings are invalid.');
      }
      return;
  }
}

final class AvaSvgCanvasDocumentDescriptorV4 {
  const AvaSvgCanvasDocumentDescriptorV4({
    required this.generationId,
    required this.sourceTurnId,
    required this.sceneId,
    required this.sceneRevision,
    required this.svgPath,
    required this.manifestPath,
    required this.svgSha256,
    required this.manifestSha256,
    required this.svgByteLength,
    required this.manifestByteLength,
  });

  final String generationId;
  final String sourceTurnId;
  final String sceneId;
  final int sceneRevision;
  final String svgPath;
  final String manifestPath;
  final String svgSha256;
  final String manifestSha256;
  final int svgByteLength;
  final int manifestByteLength;

  factory AvaSvgCanvasDocumentDescriptorV4.fromJson(
    Map<String, Object?> json,
  ) {
    _requireExactKeys(
      json,
      const {
        'protocolVersion',
        'event',
        'sessionId',
        'sequence',
        'revision',
        'generationId',
        'sourceTurnId',
        'sceneId',
        'sceneRevision',
        'svgPath',
        'manifestPath',
        'svgSha256',
        'manifestSha256',
        'svgByteLength',
        'manifestByteLength',
        'rendererCapabilityVersion',
        'validatorVersion',
        'manifestVersion',
      },
      'canvasDocumentAvailable',
    );
    if (json['protocolVersion'] != 4 ||
        json['event'] != 'canvasDocumentAvailable') {
      throw const FormatException(
        'Canvas document descriptor requires protocol v4.',
      );
    }
    if (json['rendererCapabilityVersion'] != avaSvgRendererCapabilityVersion ||
        json['validatorVersion'] != avaSvgValidatorVersion ||
        json['manifestVersion'] != avaSvgManifestVersion) {
      throw const FormatException(
        'Canvas document descriptor capability version is unsupported.',
      );
    }
    final svgPath = _boundedString(
      json,
      'svgPath',
      'canvasDocumentAvailable',
      2048,
    );
    final manifestPath = _boundedString(
      json,
      'manifestPath',
      'canvasDocumentAvailable',
      2048,
    );
    if (!svgPath.startsWith('/') || !manifestPath.startsWith('/')) {
      throw const FormatException(
        'Canvas document paths must be absolute origin-relative paths.',
      );
    }
    final svgHash = _requiredString(
      json,
      'svgSha256',
      'canvasDocumentAvailable',
    );
    final manifestHash = _requiredString(
      json,
      'manifestSha256',
      'canvasDocumentAvailable',
    );
    if (!_sha256.hasMatch(svgHash) || !_sha256.hasMatch(manifestHash)) {
      throw const FormatException(
        'Canvas document descriptor contains an invalid SHA-256 digest.',
      );
    }
    final svgLength = _requiredInt(
      json,
      'svgByteLength',
      'canvasDocumentAvailable',
    );
    final manifestLength = _requiredInt(
      json,
      'manifestByteLength',
      'canvasDocumentAvailable',
    );
    if (svgLength <= 0 ||
        svgLength > avaSvgMaximumDocumentBytes ||
        manifestLength <= 0 ||
        manifestLength > avaSvgMaximumManifestBytes) {
      throw const FormatException(
        'Canvas document descriptor byte length exceeds its bound.',
      );
    }
    return AvaSvgCanvasDocumentDescriptorV4(
      generationId: _requiredIdentifier(
        json,
        'generationId',
        'canvasDocumentAvailable',
      ),
      sourceTurnId: _requiredIdentifier(
        json,
        'sourceTurnId',
        'canvasDocumentAvailable',
      ),
      sceneId: _requiredIdentifier(json, 'sceneId', 'canvasDocumentAvailable'),
      sceneRevision: _boundedPositiveInt(
        json,
        'sceneRevision',
        'canvasDocumentAvailable',
        10001,
      ),
      svgPath: svgPath,
      manifestPath: manifestPath,
      svgSha256: svgHash,
      manifestSha256: manifestHash,
      svgByteLength: svgLength,
      manifestByteLength: manifestLength,
    );
  }
}

final class AvaValidatedSvgCanvasDocument {
  AvaValidatedSvgCanvasDocument._({
    required this.descriptor,
    required this.svgBytes,
    required this.manifestBytes,
    required this.svgText,
    required this.manifest,
  });

  final AvaSvgCanvasDocumentDescriptorV4 descriptor;
  final Uint8List svgBytes;
  final Uint8List manifestBytes;
  final String svgText;
  final AvaSvgCanvasManifest manifest;

  factory AvaValidatedSvgCanvasDocument.verify({
    required AvaSvgCanvasDocumentDescriptorV4 descriptor,
    required Uint8List svgBytes,
    required Uint8List manifestBytes,
  }) {
    if (svgBytes.length != descriptor.svgByteLength ||
        manifestBytes.length != descriptor.manifestByteLength) {
      throw const FormatException(
        'Downloaded SVG document byte length does not match its descriptor.',
      );
    }
    if (sha256.convert(svgBytes).toString() != descriptor.svgSha256 ||
        sha256.convert(manifestBytes).toString() != descriptor.manifestSha256) {
      throw const FormatException(
        'Downloaded SVG document content hash does not match its descriptor.',
      );
    }
    final String svgText;
    final Object? decodedManifest;
    try {
      svgText = utf8.decode(svgBytes, allowMalformed: false);
      decodedManifest = jsonDecode(
        utf8.decode(manifestBytes, allowMalformed: false),
      );
    } on FormatException catch (error) {
      throw FormatException(
        'Downloaded SVG document is not strict UTF-8 JSON/XML text: '
        '${error.message}',
      );
    }
    if (decodedManifest is! Map) {
      throw const FormatException('Downloaded SVG manifest must be an object.');
    }
    return AvaValidatedSvgCanvasDocument._(
      descriptor: descriptor,
      svgBytes: Uint8List.fromList(svgBytes),
      manifestBytes: Uint8List.fromList(manifestBytes),
      svgText: svgText,
      manifest: AvaSvgCanvasManifest.fromJson(
        decodedManifest.cast<String, Object?>(),
      ),
    );
  }
}

void _requireExactKeys(
  Map<String, Object?> json,
  Set<String> expected,
  String label,
) {
  final actual = json.keys.toSet();
  if (actual.length != expected.length || !actual.containsAll(expected)) {
    throw FormatException('$label contains missing or unknown fields.');
  }
}

String _requiredString(
  Map<String, Object?> json,
  String key,
  String label,
) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw FormatException('$label.$key must be a non-empty string.');
  }
  return value;
}

String _boundedString(
  Map<String, Object?> json,
  String key,
  String label,
  int maximum,
) {
  final value = _requiredString(json, key, label);
  if (value.length > maximum) {
    throw FormatException('$label.$key exceeds its character limit.');
  }
  return value;
}

String _requiredIdentifier(
  Map<String, Object?> json,
  String key,
  String label,
) {
  final value = _requiredString(json, key, label);
  if (!_identifier.hasMatch(value)) {
    throw FormatException('$label.$key is not a protocol identifier.');
  }
  return value;
}

int _requiredInt(Map<String, Object?> json, String key, String label) {
  final value = json[key];
  if (value is! int) {
    throw FormatException('$label.$key must be an integer.');
  }
  return value;
}

int _boundedPositiveInt(
  Map<String, Object?> json,
  String key,
  String label,
  int maximum,
) {
  final value = _requiredInt(json, key, label);
  if (value <= 0 || value > maximum) {
    throw FormatException('$label.$key is outside its bound.');
  }
  return value;
}
