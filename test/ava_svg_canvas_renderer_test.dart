import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_renderer.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

extension on AvaSvgCanvasHtmlRenderer {
  String build({
    required AvaValidatedSvgCanvasDocument document,
    required String preparedHandler,
    required String presentedHandler,
    required String bindingHandler,
    required String errorHandler,
  }) =>
      buildShell(
        preparedHandler: preparedHandler,
        presentedHandler: presentedHandler,
        draftPresentedHandler: 'draft_presented_handler',
        bindingHandler: bindingHandler,
        errorHandler: errorHandler,
      ) +
      mountCandidateScript(document);
}

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

  test('selected accessibility hitbox remains visually transparent', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(html, contains('min-width:44px'));
    expect(html, contains('min-height:44px'));
    expect(html, contains('.ava-hit.ava-selected'));
    expect(
      html,
      contains(
        '.ava-hit.ava-selected{border:0;background:transparent;outline:0;box-shadow:none}',
      ),
    );
    expect(html, isNot(contains('border:3px solid')));
    expect(html, isNot(contains('background:rgba(69,179,166')));
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
    expect(html, contains("script-src 'nonce-ava-svg-runtime-v2'"));
    expect(html, contains('frame-src blob:'));
    expect(html, contains('Math.max(44, rect.width)'));
    expect(html, contains('window.avaCanvas'));
    expect(html, contains('replaceDraft(payload)'));
    expect(html, contains('mountCandidate(payload)'));
    expect(html, contains('activateCandidate(identity, interactive)'));
    expect(html, contains('const previousActive = active; active = candidate;'));
    expect(html, contains('requestAnimationFrame(() => requestAnimationFrame'));
    expect(
      html,
      contains(
        "parsed.documentElement.namespaceURI !== 'http://www.w3.org/2000/svg'",
      ),
    );
  });

  test('duplicate local IDs resolve only in each layer iframe document', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(html, contains("document.createElement('iframe')"));
    expect(
        html, contains("frame.setAttribute('sandbox', 'allow-same-origin')"));
    expect(html, contains('URL.createObjectURL(new Blob([frameShell]'));
    expect(html, contains("frame-src 'none'"));
    expect(html, contains("script-src 'none'"));
    expect(html, contains('target.document.getElementById(id)'));
    expect(html, contains('scopedDocument.getElementById(id)'));
    expect(html, contains('svg.contains(resolved)'));
    expect(html, contains("attribute.localName === 'href'"));
    expect(html, contains("attribute.name === 'xlink:href'"));
    expect(html, contains("'marker-start','marker-mid','marker-end'"));
    expect(
      html,
      contains(
        'if (previousFrame && previousFrame !== frame) previousFrame.remove()',
      ),
    );
    expect(
      html,
      contains('if (previousBlobUrl) URL.revokeObjectURL(previousBlobUrl)'),
    );
    expect(html, isNot(contains('target.host.replaceChildren(frame)')));
    expect(html, contains('isVisibleGeometryValid'));
    expect(html, contains('isDocumentScopeValid'));
    expect(html, contains("new Error('documentScopeLoadTimeout')"));
    expect(html, contains("diagnose(operation + '.iframeTimeout', identity)"));
    expect(html, contains('clearTimeout(timeout)'));
    expect(
        html, contains('const previousActive = active; active = candidate;'));
    expect(html, isNot(contains("document.querySelector('#")));
    expect(html, isNot(contains("setAttribute('id'")));
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
      contains(
        "bridge('binding_handler', {bindingId:binding.bindingId, "
        'generationId:payload.identity.generationId})',
      ),
    );
    expect(html, isNot(contains('{bindingId: binding.bindingId, label:')));
  });

  test('typed runtime has persistent explore and select state', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(html, contains('let selectedBindingId = null'));
    expect(html, contains("manifest.interactionMode === 'explore'"));
    expect(html, contains('previous.binding.revealIds'));
    expect(html, contains('selectedBindingId = binding.bindingId'));
    expect(html, contains("classList.add('ava-selected')"));
    expect(html, contains('new ResizeObserver(() =>'));
  });

  test('explore and select report only a change of selection', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(
      html,
      contains(
        "if (manifest.interactionMode !== 'steps' && "
        'selectedBindingId === binding.bindingId) return;',
      ),
    );
    expect(
      html.indexOf('selectedBindingId === binding.bindingId'),
      lessThan(html.indexOf('report(binding);')),
    );
  });

  test('steps use renderer-owned controls and do not report initial state', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(html, contains("back.textContent = 'Back'"));
    expect(html, contains("next.textContent = 'Next'"));
    expect(html, contains("currentStep + ' / ' + stepBindings.length"));
    expect(html, contains('applyStep(currentStep + 1, true)'));
    expect(html, contains('applyStep(currentStep - 1, true)'));
    expect(html, contains("applyStep(1, false)"));
    expect(html, contains("if (manifest.interactionMode !== 'steps')"));
    expect(
      html,
      isNot(
        contains(
          'if (selectedBindingId === binding.bindingId) return;',
        ),
      ),
    );
  });

  test('legacy generic action runtime is absent', () {
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: document,
      preparedHandler: 'prepared_handler',
      presentedHandler: 'presented_handler',
      bindingHandler: 'binding_handler',
      errorHandler: 'error_handler',
    );

    expect(html, isNot(contains('binding.action')));
    expect(html, isNot(contains('binding.pulse')));
    expect(html, isNot(contains('binding.exclusive')));
    expect(html, isNot(contains('binding.serverReportable')));
    expect(html, isNot(contains('binding.declaredValue')));
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
