import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import '../canvas/ava_svg_canvas_telemetry.dart';
import '../models/ava_svg_canvas_protocol.dart';

final class AvaSvgDocumentFetchException implements Exception {
  const AvaSvgDocumentFetchException({
    required this.message,
    this.statusCode,
    this.requestId,
  });

  final String message;
  final int? statusCode;
  final String? requestId;

  @override
  String toString() {
    final details = <String>[
      if (statusCode != null) 'status=$statusCode',
      if (requestId != null) 'requestId=$requestId',
    ];
    return details.isEmpty
        ? 'AvaSvgDocumentFetchException: $message'
        : 'AvaSvgDocumentFetchException: $message (${details.join(', ')})';
  }
}

final class AvaSvgDocumentFetchResponse {
  const AvaSvgDocumentFetchResponse({
    required this.statusCode,
    required this.contentType,
    required this.protocolVersion,
    required this.contentSha256,
    required this.cacheControl,
    required this.bytes,
    this.requestId,
  });

  final int statusCode;
  final String? contentType;
  final String? protocolVersion;
  final String? contentSha256;
  final String? cacheControl;
  final Stream<List<int>> bytes;
  final String? requestId;
}

abstract interface class AvaSvgDocumentTransport {
  Future<AvaSvgDocumentFetchResponse> get({
    required Uri endpoint,
    required Map<String, String> headers,
    required Duration timeout,
  });

  void close();
}

final class IoAvaSvgDocumentTransport implements AvaSvgDocumentTransport {
  IoAvaSvgDocumentTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  final HttpClient _client;

  @override
  Future<AvaSvgDocumentFetchResponse> get({
    required Uri endpoint,
    required Map<String, String> headers,
    required Duration timeout,
  }) async {
    final request = await _client.getUrl(endpoint).timeout(timeout);
    request.followRedirects = false;
    headers.forEach(request.headers.set);
    final response = await request.close().timeout(timeout);
    return AvaSvgDocumentFetchResponse(
      statusCode: response.statusCode,
      contentType: response.headers.contentType?.mimeType,
      protocolVersion: response.headers.value('x-ava-protocol-version'),
      contentSha256: response.headers.value('x-content-sha256'),
      cacheControl: response.headers.value(HttpHeaders.cacheControlHeader),
      bytes: response.timeout(timeout),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

abstract interface class AvaSvgDocumentLoader {
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  );

  void close();
}

final class AvaSvgDocumentService implements AvaSvgDocumentLoader {
  const AvaSvgDocumentService({
    required Uri documentBaseUrl,
    required String canvasToken,
    required Duration timeout,
    required AvaSvgDocumentTransport transport,
    AvaSvgCanvasTelemetrySink? telemetry,
  })  : _documentBaseUrl = documentBaseUrl,
        _canvasToken = canvasToken,
        _timeout = timeout,
        _transport = transport,
        _telemetry = telemetry;

  final Uri _documentBaseUrl;
  final String _canvasToken;
  final Duration _timeout;
  final AvaSvgDocumentTransport _transport;
  final AvaSvgCanvasTelemetrySink? _telemetry;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async {
    final total = Stopwatch()..start();
    _record('canvas.document.fetch_started', descriptor);
    try {
      final svgUri = _resolveDocumentPath(descriptor.svgPath);
      final manifestUri = _resolveDocumentPath(descriptor.manifestPath);
      final headers = Stopwatch()..start();
      final responses = await Future.wait([
        _transport.get(
          endpoint: svgUri,
          headers: _headers('image/svg+xml'),
          timeout: _timeout,
        ),
        _transport.get(
          endpoint: manifestUri,
          headers: _headers('application/json'),
          timeout: _timeout,
        ),
      ]);
      headers.stop();
      _record(
        'canvas.document.headers_received',
        descriptor,
        stageDuration: headers.elapsed,
        fields: {
          'svgStatusCode': responses[0].statusCode,
          'manifestStatusCode': responses[1].statusCode,
        },
      );

      final svgRead = Stopwatch()..start();
      final svgBytes = await _read(
        responses[0],
        expectedContentType: 'image/svg+xml',
        expectedHash: descriptor.svgSha256,
        expectedLength: descriptor.svgByteLength,
        maximumLength: avaSvgMaximumDocumentBytes,
      );
      svgRead.stop();
      _record(
        'canvas.document.svg_body_received',
        descriptor,
        stageDuration: svgRead.elapsed,
        fields: {'svgByteLength': svgBytes.length},
      );

      final manifestRead = Stopwatch()..start();
      final manifestBytes = await _read(
        responses[1],
        expectedContentType: 'application/json',
        expectedHash: descriptor.manifestSha256,
        expectedLength: descriptor.manifestByteLength,
        maximumLength: avaSvgMaximumManifestBytes,
      );
      manifestRead.stop();
      _record(
        'canvas.document.manifest_body_received',
        descriptor,
        stageDuration: manifestRead.elapsed,
        fields: {'manifestByteLength': manifestBytes.length},
      );

      final verification = Stopwatch()..start();
      final document = AvaValidatedSvgCanvasDocument.verify(
        descriptor: descriptor,
        svgBytes: svgBytes,
        manifestBytes: manifestBytes,
      );
      verification.stop();
      _record(
        'canvas.document.hash_verified',
        descriptor,
        stageDuration: verification.elapsed,
        fields: {
          'svgByteLength': svgBytes.length,
          'manifestByteLength': manifestBytes.length,
        },
      );
      total.stop();
      _record(
        'canvas.document.fetch_completed',
        descriptor,
        stageDuration: total.elapsed,
      );
      return document;
    } on Object catch (error) {
      total.stop();
      _record(
        'canvas.document.fetch_failed',
        descriptor,
        stageDuration: total.elapsed,
        fields: {'failureType': error.runtimeType.toString()},
      );
      rethrow;
    }
  }

  @override
  void close() => _transport.close();

  void _record(
    String stage,
    AvaSvgCanvasDocumentDescriptorV4 descriptor, {
    Duration stageDuration = Duration.zero,
    Map<String, Object?> fields = const {},
  }) {
    _telemetry?.call(
      AvaSvgCanvasTelemetryEvent(
        stage: stage,
        stageDuration: stageDuration,
        fields: {
          'generationId': descriptor.generationId,
          'sourceTurnId': descriptor.sourceTurnId,
          'sceneId': descriptor.sceneId,
          'sceneRevision': descriptor.sceneRevision,
          'svgSha256': descriptor.svgSha256,
          'manifestSha256': descriptor.manifestSha256,
          ...fields,
        },
      ),
    );
  }

  Map<String, String> _headers(String accept) => {
        HttpHeaders.acceptHeader: accept,
        'x-ava-protocol-version': '4',
        'x-ava-canvas-token': _canvasToken,
      };

  Uri _resolveDocumentPath(String path) {
    final base = _documentBaseUrl;
    final basePath = base.path.endsWith('/') ? base.path : '${base.path}/';
    if (!base.hasScheme ||
        !base.hasAuthority ||
        base.userInfo.isNotEmpty ||
        base.query.isNotEmpty ||
        base.fragment.isNotEmpty ||
        !path.startsWith(basePath)) {
      throw const FormatException(
        'Canvas document path escaped its authenticated base URL.',
      );
    }
    final resolved = base.replace(path: path, query: null, fragment: null);
    if (resolved.scheme != base.scheme ||
        resolved.host != base.host ||
        resolved.port != base.port) {
      throw const FormatException(
        'Canvas document path changed its authenticated origin.',
      );
    }
    return resolved;
  }

  Future<Uint8List> _read(
    AvaSvgDocumentFetchResponse response, {
    required String expectedContentType,
    required String expectedHash,
    required int expectedLength,
    required int maximumLength,
  }) async {
    if (response.statusCode != HttpStatus.ok) {
      await _drainBounded(response.bytes, maximumLength: 4096);
      throw AvaSvgDocumentFetchException(
        message: 'Ava backend rejected the canvas document fetch.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.protocolVersion != '4' ||
        response.contentType != expectedContentType ||
        response.contentSha256 != expectedHash ||
        response.cacheControl?.toLowerCase() != 'no-store') {
      await _drainBounded(response.bytes, maximumLength: maximumLength);
      throw AvaSvgDocumentFetchException(
        message: 'Ava backend returned invalid canvas document metadata.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    return _readExactBounded(
      response.bytes,
      expectedLength: expectedLength,
      maximumLength: maximumLength,
    ).timeout(_timeout);
  }

  static Future<Uint8List> _readExactBounded(
    Stream<List<int>> source, {
    required int expectedLength,
    required int maximumLength,
  }) async {
    final builder = BytesBuilder(copy: false);
    var length = 0;
    await for (final chunk in source) {
      length += chunk.length;
      if (length > expectedLength || length > maximumLength) {
        throw const FormatException(
          'Canvas document response exceeded its declared byte length.',
        );
      }
      builder.add(chunk);
    }
    if (length != expectedLength) {
      throw const FormatException(
        'Canvas document response ended before its declared byte length.',
      );
    }
    return builder.takeBytes();
  }

  static Future<void> _drainBounded(
    Stream<List<int>> source, {
    required int maximumLength,
  }) async {
    var length = 0;
    await for (final chunk in source) {
      length += chunk.length;
      if (length > maximumLength) return;
    }
  }
}
