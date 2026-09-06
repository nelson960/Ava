import 'dart:convert';
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

  Future<void> synchronizeWorkspace({
    required AvaSessionBootstrapV4 bootstrap,
    required Map<String, Object?> manifest,
  }) async {
    final body = utf8.encode(jsonEncode(manifest));
    if (body.length > 32 * 1024) {
      throw const FormatException(
        'Canvas workspace manifest exceeds its transport bound.',
      );
    }
    final response = await _transport.putBytes(
      endpoint: _configuration.canvasWorkspaceEndpoint(bootstrap.sessionId),
      headers: {
        HttpHeaders.acceptHeader: 'application/json',
        HttpHeaders.contentTypeHeader: 'application/json',
        'x-ava-protocol-version': '$avaRealtimeProtocolVersionV4',
        'x-ava-canvas-token': bootstrap.canvasToken,
      },
      body: body,
      timeout: _configuration.requestTimeout,
    );
    await _drain(response.bytes);
    if (response.statusCode != HttpStatus.ok ||
        response.protocolVersion != '$avaRealtimeProtocolVersionV4') {
      throw AvaCanvasRestoreException(
        message: 'Ava backend rejected local Canvas workspace synchronization.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
  }

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

  Future<void> focusScene({
    required int focusRevision,
    required AvaSessionBootstrapV4 bootstrap,
    required AvaValidatedSvgCanvasDocument document,
  }) async {
    final descriptor = document.descriptor;
    final response = await _transport.putBytes(
      endpoint: _configuration.canvasFocusEndpoint(bootstrap.sessionId),
      headers: {
        'x-ava-focus-revision': '$focusRevision',
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
    final responseBytes = await _readBounded(response.bytes);
    if (response.statusCode == HttpStatus.conflict &&
        response.protocolVersion == '$avaRealtimeProtocolVersionV4' &&
        _isFocusRevisionConflict(responseBytes)) {
      return;
    }
    if (response.statusCode != HttpStatus.noContent ||
        response.protocolVersion != '$avaRealtimeProtocolVersionV4') {
      throw AvaCanvasRestoreException(
        message: 'Ava backend rejected local Canvas focus.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
  }

  Future<void> _drain(Stream<List<int>> source) async {
    await _readBounded(source);
  }

  Future<List<int>> _readBounded(Stream<List<int>> source) async {
    final bytes = <int>[];
    var length = 0;
    await for (final chunk in source) {
      length += chunk.length;
      if (length > 4096) {
        throw const FormatException(
          'Canvas restore response exceeded its error-body bound.',
        );
      }
      bytes.addAll(chunk);
    }
    return bytes;
  }

  bool _isFocusRevisionConflict(List<int> bytes) {
    if (bytes.isEmpty) return false;
    final Object? decoded;
    try {
      decoded = jsonDecode(utf8.decode(bytes, allowMalformed: false));
    } on FormatException {
      return false;
    }
    if (decoded is! Map<String, Object?>) return false;
    final error = decoded['error'];
    return error is Map<String, Object?> &&
        error['code'] == 'focusRevisionConflict';
  }
}
