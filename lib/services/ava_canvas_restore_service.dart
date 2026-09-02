import 'dart:io';

import '../config/ava_backend_configuration.dart';
import '../models/ava_realtime_protocol_v4.dart';
import '../models/ava_svg_canvas_protocol.dart';

final class AvaCanvasRestoreResponse {
  const AvaCanvasRestoreResponse({
    required this.statusCode,
    required this.protocolVersion,
    required this.bytes,
    this.requestId,
  });

  final int statusCode;
  final String? protocolVersion;
  final Stream<List<int>> bytes;
  final String? requestId;
}

abstract interface class AvaCanvasRestoreTransport {
  Future<AvaCanvasRestoreResponse> putBytes({
    required Uri endpoint,
    required Map<String, String> headers,
    required List<int> body,
    required Duration timeout,
  });

  void close();
}

final class IoAvaCanvasRestoreTransport implements AvaCanvasRestoreTransport {
  IoAvaCanvasRestoreTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  final HttpClient _client;

  @override
  Future<AvaCanvasRestoreResponse> putBytes({
    required Uri endpoint,
    required Map<String, String> headers,
    required List<int> body,
    required Duration timeout,
  }) async {
    final request = await _client.putUrl(endpoint).timeout(timeout);
    headers.forEach(request.headers.set);
    request.contentLength = body.length;
    request.add(body);
    final response = await request.close().timeout(timeout);
    return AvaCanvasRestoreResponse(
      statusCode: response.statusCode,
      protocolVersion: response.headers.value('x-ava-protocol-version'),
      bytes: response.timeout(timeout),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

final class AvaCanvasRestoreException implements Exception {
  const AvaCanvasRestoreException({
    required this.message,
    this.statusCode,
    this.requestId,
  });

  final String message;
  final int? statusCode;
  final String? requestId;

  @override
  String toString() => 'AvaCanvasRestoreException: $message';
}

final class AvaCanvasRestoreService {
  const AvaCanvasRestoreService({
    required AvaBackendConfiguration configuration,
    required AvaCanvasRestoreTransport transport,
  })  : _configuration = configuration,
        _transport = transport;

  final AvaBackendConfiguration _configuration;
  final AvaCanvasRestoreTransport _transport;

  Future<void> restoreFocused({
    required AvaSessionBootstrapV4 bootstrap,
    required AvaValidatedSvgCanvasDocument document,
  }) async {
    final descriptor = document.descriptor;
    final response = await _transport.putBytes(
      endpoint: _configuration.canvasRestoreEndpoint(bootstrap.sessionId),
      headers: {
        HttpHeaders.acceptHeader: 'application/json',
        HttpHeaders.contentTypeHeader: 'application/octet-stream',
        'x-ava-protocol-version': '$avaRealtimeProtocolVersionV4',
        'x-ava-canvas-token': bootstrap.canvasToken,
        'x-ava-generation-id': descriptor.generationId,
        'x-ava-source-turn-id': descriptor.sourceTurnId,
        'x-ava-scene-id': descriptor.sceneId,
        'x-ava-scene-revision': '${descriptor.sceneRevision}',
        'x-ava-svg-sha256': descriptor.svgSha256,
        'x-ava-manifest-sha256': descriptor.manifestSha256,
      },
      body: document.svgBytes,
      timeout: _configuration.requestTimeout,
    );
    await _drain(response.bytes);
    if (response.statusCode != HttpStatus.noContent ||
        response.protocolVersion != '$avaRealtimeProtocolVersionV4') {
      throw AvaCanvasRestoreException(
        message: 'Ava backend rejected local Canvas restoration.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
  }

  Future<void> _drain(Stream<List<int>> source) async {
    var length = 0;
    await for (final chunk in source) {
      length += chunk.length;
      if (length > 4096) {
        throw const FormatException(
          'Canvas restore response exceeded its error-body bound.',
        );
      }
    }
  }
}
