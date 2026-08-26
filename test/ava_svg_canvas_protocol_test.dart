import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

void main() {
  late Map<String, Object?> descriptorJson;
  late Uint8List svgBytes;
  late Uint8List manifestBytes;

  setUpAll(() {
    final events = jsonDecode(
      File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
          .readAsStringSync(),
    ) as List;
    descriptorJson = (events.first as Map).cast<String, Object?>();
    svgBytes =
        File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync();
    manifestBytes = Uint8List.fromList(utf8.encode(_manifestJson));
  });

  test('shared v4 fixture preserves and verifies exact UTF-8 SVG bytes', () {
    final descriptor =
        AvaSvgCanvasDocumentDescriptorV4.fromJson(descriptorJson);

    final document = AvaValidatedSvgCanvasDocument.verify(
      descriptor: descriptor,
      svgBytes: svgBytes,
      manifestBytes: manifestBytes,
    );

    expect(document.svgBytes, orderedEquals(svgBytes));
    expect(utf8.encode(document.svgText), orderedEquals(svgBytes));
    expect(document.svgText.startsWith('<svg'), isTrue);
    expect(document.svgText.endsWith('</svg>\n'), isTrue);
    expect(document.manifest.title, 'Café cloud ☁️');
    expect(document.manifest.bindings.entries.single.key, 'select-cloud');
    expect(document.manifest.interactionMode, AvaSvgInteractionMode.select);
    expect(document.manifest.bindings['select-cloud']!.stepIndex, isNull);
  });

  test('one changed SVG byte fails content identity', () {
    final descriptor =
        AvaSvgCanvasDocumentDescriptorV4.fromJson(descriptorJson);
    final changed = Uint8List.fromList(svgBytes);
    changed[100] ^= 1;

    expect(
      () => AvaValidatedSvgCanvasDocument.verify(
        descriptor: descriptor,
        svgBytes: changed,
        manifestBytes: manifestBytes,
      ),
      throwsFormatException,
    );
  });

  test('descriptor rejects unknown fields and noncanonical versions', () {
    final unknown = Map<String, Object?>.of(descriptorJson)
      ..['rawSvg'] = '<svg/>';
    final wrongVersion = Map<String, Object?>.of(descriptorJson)
      ..['rendererCapabilityVersion'] = 'different-renderer';

    expect(
      () => AvaSvgCanvasDocumentDescriptorV4.fromJson(unknown),
      throwsFormatException,
    );
    expect(
      () => AvaSvgCanvasDocumentDescriptorV4.fromJson(wrongVersion),
      throwsFormatException,
    );
  });

  test('legacy generic binding metadata is rejected', () {
    final manifest = (jsonDecode(_manifestJson) as Map).cast<String, Object?>();
    final bindings = manifest['bindings']! as List;
    final binding = Map<String, Object?>.of(
      (bindings.single as Map).cast<String, Object?>(),
    )..['action'] = 'select';
    manifest['bindings'] = [binding];
    final invalidManifestBytes = Uint8List.fromList(
      utf8.encode(jsonEncode(manifest)),
    );
    final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson({
      ...descriptorJson,
      'manifestByteLength': invalidManifestBytes.length,
      // Hash validation occurs before manifest parsing, so use a direct parser
      // assertion for the semantic trust boundary.
    });
    expect(descriptor.manifestByteLength, invalidManifestBytes.length);

    expect(
      () => AvaSvgCanvasManifest.fromJson(manifest),
      throwsFormatException,
    );
  });

  test('none mode requires an empty binding set', () {
    final manifest = (jsonDecode(_manifestJson) as Map).cast<String, Object?>()
      ..['interactionMode'] = 'none'
      ..['bindings'] = <Object?>[];

    final parsed = AvaSvgCanvasManifest.fromJson(manifest);

    expect(parsed.interactionMode, AvaSvgInteractionMode.none);
    expect(parsed.bindings, isEmpty);
  });

  test('explore mode accepts deterministic reveal targets', () {
    final manifest = (jsonDecode(_manifestJson) as Map).cast<String, Object?>()
      ..['interactionMode'] = 'explore'
      ..['bindings'] = [
        {
          'accessibilityLabel': 'Explore the cloud',
          'bindingId': 'explore-cloud',
          'elementId': 'cloud',
          'revealIds': ['rain-detail'],
          'targetId': 'cloud',
        },
      ];

    final parsed = AvaSvgCanvasManifest.fromJson(manifest);

    expect(parsed.interactionMode, AvaSvgInteractionMode.explore);
    expect(parsed.bindings['explore-cloud']!.revealIds, ['rain-detail']);
  });

  test('select mode rejects reveal metadata', () {
    final manifest = (jsonDecode(_manifestJson) as Map).cast<String, Object?>();
    final binding = Map<String, Object?>.of(
      ((manifest['bindings']! as List).single as Map).cast<String, Object?>(),
    )..['revealIds'] = ['rain-detail'];
    manifest['bindings'] = [binding];

    expect(
      () => AvaSvgCanvasManifest.fromJson(manifest),
      throwsFormatException,
    );
  });

  test('steps mode requires two or more contiguous unique indexes', () {
    final manifest = (jsonDecode(_manifestJson) as Map).cast<String, Object?>()
      ..['interactionMode'] = 'steps'
      ..['bindings'] = [
        {
          'accessibilityLabel': 'Egg stage',
          'bindingId': 'step-egg',
          'elementId': 'egg',
          'revealIds': <Object?>[],
          'stepIndex': 1,
          'targetId': 'egg',
        },
        {
          'accessibilityLabel': 'Caterpillar stage',
          'bindingId': 'step-caterpillar',
          'elementId': 'caterpillar',
          'revealIds': <Object?>[],
          'stepIndex': 2,
          'targetId': 'caterpillar',
        },
      ];

    final parsed = AvaSvgCanvasManifest.fromJson(manifest);

    expect(parsed.interactionMode, AvaSvgInteractionMode.steps);
    expect(parsed.bindings['step-caterpillar']!.stepIndex, 2);

    final skipped =
        (jsonDecode(jsonEncode(manifest)) as Map).cast<String, Object?>();
    final skippedBindings = skipped['bindings']! as List;
    (skippedBindings[1] as Map)['stepIndex'] = 3;
    expect(
      () => AvaSvgCanvasManifest.fromJson(skipped),
      throwsFormatException,
    );
  });

  test('unknown interaction mode is rejected without fallback', () {
    final manifest = (jsonDecode(_manifestJson) as Map).cast<String, Object?>()
      ..['interactionMode'] = 'interactive';

    expect(
      () => AvaSvgCanvasManifest.fromJson(manifest),
      throwsFormatException,
    );
  });
}
