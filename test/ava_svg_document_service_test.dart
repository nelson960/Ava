import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/canvas/ava_svg_canvas_telemetry.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","action":"select","bindingId":"select-cloud","elementId":"cloud","exclusive":false,"hitTest":"element","pulseIds":[],"revealIds":[],"serverReportable":true,"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","manifestVersion":"ava-svg-manifest-v1","rendererCapabilityVersion":"ava-svg-renderer-v1","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v1","viewBox":[0.0,0.0,1000.0,600.0]}';

final class _Request {
  const _Request(this.endpoint, this.headers);

  final Uri endpoint;
  final Map<String, String> headers;
}

final class _Transport implements AvaSvgDocumentTransport {
  _Transport(this.responses);

  final Map<String, AvaSvgDocumentFetchResponse> responses;
  final List<_Request> requests = [];
  bool closed = false;

  @override
  Future<AvaSvgDocumentFetchResponse> get({
    required Uri endpoint,
    required Map<String, String> headers,
    required Duration timeout,
  }) async {
    expect(timeout, const Duration(seconds: 2));
    requests.add(_Request(endpoint, headers));
    final response = responses[endpoint.path];
    if (response == null) throw StateError('unexpected path ${endpoint.path}');
    return response;
  }

  @override
  void close() => closed = true;
}

void main() {
  late AvaSvgCanvasDocumentDescriptorV4 descriptor;
  late Uint8List svgBytes;
  late Uint8List manifestBytes;

  setUpAll(() {
    final events = jsonDecode(
      File('protocol/fixtures/ava_realtime_canvas_events_v4.json')
          .readAsStringSync(),
    ) as List;
    descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson(
      (events.first as Map).cast<String, Object?>(),
    );
    svgBytes =
        File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync();
    manifestBytes = Uint8List.fromList(utf8.encode(_manifestJson));
  });

  test('fetches SVG and manifest independently with scoped auth', () async {
    final telemetry = <AvaSvgCanvasTelemetryEvent>[];
    final transport = _Transport({
      descriptor.svgPath: _response(
        bytes: svgBytes,
        contentType: 'image/svg+xml',
        hash: descriptor.svgSha256,
      ),
      descriptor.manifestPath: _response(
        bytes: manifestBytes,
        contentType: 'application/json',
        hash: descriptor.manifestSha256,
      ),
    });
    final service = AvaSvgDocumentService(
      documentBaseUrl: Uri.parse(
        'https://ava.example.test/v4/realtime/sessions/session-1234/'
        'canvas-documents',
      ),
      canvasToken: 'canvas-token',
      timeout: const Duration(seconds: 2),
      transport: transport,
      telemetry: telemetry.add,
    );

    final document = await service.fetch(descriptor);

    expect(document.svgBytes, orderedEquals(svgBytes));
    expect(transport.requests, hasLength(2));
    expect(
      transport.requests.map((request) => request.endpoint.path),
      containsAll([descriptor.svgPath, descriptor.manifestPath]),
    );
    for (final request in transport.requests) {
      expect(request.endpoint.host, 'ava.example.test');
      expect(request.headers['x-ava-protocol-version'], '4');
      expect(request.headers['x-ava-canvas-token'], 'canvas-token');
    }
    expect(
      telemetry.map((event) => event.stage),
      [
        'canvas.document.fetch_started',
        'canvas.document.headers_received',
        'canvas.document.svg_body_received',
        'canvas.document.manifest_body_received',
        'canvas.document.hash_verified',
        'canvas.document.fetch_completed',
      ],
    );
    for (final event in telemetry) {
      expect(event.fields['generationId'], descriptor.generationId);
      expect(event.fields['sourceTurnId'], descriptor.sourceTurnId);
      expect(event.fields, isNot(contains('canvasToken')));
      expect(event.fields, isNot(contains('url')));
      expect(event.stageDuration.isNegative, isFalse);
    }
    final verified = telemetry.firstWhere(
      (event) => event.stage == 'canvas.document.hash_verified',
    );
    expect(verified.fields['svgByteLength'], svgBytes.length);
    expect(verified.fields['manifestByteLength'], manifestBytes.length);
    expect(verified.fields['svgSha256'], descriptor.svgSha256);
    service.close();
    expect(transport.closed, isTrue);
  });

  test('fails before parsing when response metadata is not content-bound', () {
    final transport = _Transport({
      descriptor.svgPath: AvaSvgDocumentFetchResponse(
        statusCode: 200,
        contentType: 'image/svg+xml',
        protocolVersion: '4',
        contentSha256: List.filled(64, '0').join(),
        cacheControl: 'no-store',
        bytes: Stream.value(svgBytes),
      ),
      descriptor.manifestPath: _response(
        bytes: manifestBytes,
        contentType: 'application/json',
        hash: descriptor.manifestSha256,
      ),
    });
    final service = AvaSvgDocumentService(
      documentBaseUrl: Uri.parse(
        'https://ava.example.test/v4/realtime/sessions/session-1234/'
        'canvas-documents',
      ),
      canvasToken: 'canvas-token',
      timeout: const Duration(seconds: 2),
      transport: transport,
    );

    expect(
      () => service.fetch(descriptor),
      throwsA(isA<AvaSvgDocumentFetchException>()),
    );
  });

  test('rejects descriptor path outside authenticated base before network', () {
    final outside = AvaSvgCanvasDocumentDescriptorV4.fromJson({
      'protocolVersion': 4,
      'event': 'canvasDocumentAvailable',
      'sessionId': 'session-1234',
      'sequence': 1,
      'revision': 1,
      'generationId': descriptor.generationId,
      'sourceTurnId': descriptor.sourceTurnId,
      'sceneId': descriptor.sceneId,
      'sceneRevision': descriptor.sceneRevision,
      'svgPath': '/different/svg',
      'manifestPath': '/different/manifest',
      'svgSha256': descriptor.svgSha256,
      'manifestSha256': descriptor.manifestSha256,
      'svgByteLength': descriptor.svgByteLength,
      'manifestByteLength': descriptor.manifestByteLength,
      'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
      'validatorVersion': avaSvgValidatorVersion,
      'manifestVersion': avaSvgManifestVersion,
    });
    final transport = _Transport({});
    final service = AvaSvgDocumentService(
      documentBaseUrl: Uri.parse(
        'https://ava.example.test/v4/realtime/sessions/session-1234/'
        'canvas-documents',
      ),
      canvasToken: 'canvas-token',
      timeout: const Duration(seconds: 2),
      transport: transport,
    );

    expect(() => service.fetch(outside), throwsFormatException);
    expect(transport.requests, isEmpty);
  });

  test('fails on truncated body instead of accepting partial SVG', () {
    final transport = _Transport({
      descriptor.svgPath: _response(
        bytes: Uint8List.sublistView(svgBytes, 0, svgBytes.length - 1),
        contentType: 'image/svg+xml',
        hash: descriptor.svgSha256,
      ),
      descriptor.manifestPath: _response(
        bytes: manifestBytes,
        contentType: 'application/json',
        hash: descriptor.manifestSha256,
      ),
    });
    final service = AvaSvgDocumentService(
      documentBaseUrl: Uri.parse(
        'https://ava.example.test/v4/realtime/sessions/session-1234/'
        'canvas-documents',
      ),
      canvasToken: 'canvas-token',
      timeout: const Duration(seconds: 2),
      transport: transport,
    );

    expect(() => service.fetch(descriptor), throwsFormatException);
  });
}

AvaSvgDocumentFetchResponse _response({
  required Uint8List bytes,
  required String contentType,
  required String hash,
}) =>
    AvaSvgDocumentFetchResponse(
      statusCode: HttpStatus.ok,
      contentType: contentType,
      protocolVersion: '4',
      contentSha256: hash,
      cacheControl: 'no-store',
      bytes: Stream.value(bytes),
    );
