import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_renderer.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","action":"select","bindingId":"select-cloud","elementId":"cloud","exclusive":false,"hitTest":"element","pulseIds":[],"revealIds":[],"serverReportable":true,"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","manifestVersion":"ava-svg-manifest-v1","rendererCapabilityVersion":"ava-svg-renderer-v1","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v1","viewBox":[0.0,0.0,1000.0,600.0]}';

void main() {
  late AvaValidatedSvgCanvasDocument document;

  setUpAll(() {
    final events = jsonDecode(
      File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
          .readAsStringSync(),
    ) as List;
    final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson(
      (events.first as Map).cast<String, Object?>(),
    );
    document = AvaValidatedSvgCanvasDocument.verify(
      descriptor: descriptor,
      svgBytes:
          File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
      manifestBytes: Uint8List.fromList(utf8.encode(_manifestJson)),
    );
  });

  test('trusted wrapper embeds exact SVG as base64 and blocks all network', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(html, contains(base64Encode(document.svgBytes)));
    expect(html, contains(base64Encode(document.manifestBytes)));
    expect(html, isNot(contains('<ellipse')));
    expect(html, contains("default-src 'none'"));
    expect(html, contains("connect-src 'none'"));
    expect(html, contains("object-src 'none'"));
    expect(html, contains("script-src 'nonce-ava-svg-runtime-v1'"));
    expect(html, contains('Math.max(44, rect.width)'));
    expect(html, contains("CSS.escape(binding.elementId)"));
    expect(html, contains('window.avaPresent'));
    expect(html, contains('requestAnimationFrame(() => requestAnimationFrame'));
  });

  test('provider binding values never become bridge-authored semantics', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(
      html,
      contains("bridge('binding_handler', {bindingId: binding.bindingId})"),
    );
    expect(html, isNot(contains('{bindingId: binding.bindingId, label:')));
  });

  test('bridge parser accepts one object and rejects additional arguments', () {
    final message = AvaSvgRendererBridgeMessage.fromArguments([
      {'bindingId': 'select-cloud'},
    ]);

    expect(message.requiredString('bindingId'), 'select-cloud');
    expect(
      () => AvaSvgRendererBridgeMessage.fromArguments([
        {'bindingId': 'select-cloud'},
        {'extra': true},
      ]),
      throwsFormatException,
    );
  });

  test('invalid bridge handler name cannot enter app-owned JavaScript', () {
    expect(
      () => const AvaSvgCanvasHtmlRenderer().build(
        document: document,
        preparedHandler: 'prepared-handler',
        presentedHandler: 'presented-handler',
        bindingHandler: 'bad handler',
        errorHandler: 'error-handler',
      ),
      throwsArgumentError,
    );
  });
}
