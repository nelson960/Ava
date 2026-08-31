import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../config/ava_backend_configuration.dart';
import '../models/ava_realtime_protocol.dart';
import '../models/ava_realtime_protocol_v4.dart';
import 'ava_device_context_service.dart';

final class AvaRealtimeBootstrapException implements Exception {
  const AvaRealtimeBootstrapException({
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
        ? 'AvaRealtimeBootstrapException: $message'
        : 'AvaRealtimeBootstrapException: $message (${details.join(', ')})';
  }
}

final class AvaRealtimeBootstrapResponse {
  const AvaRealtimeBootstrapResponse({
    required this.statusCode,
    required this.contentType,
    required this.protocolVersion,
    required this.bytes,
    this.requestId,
  });

  final int statusCode;
  final String? contentType;
  final String? protocolVersion;
  final Stream<List<int>> bytes;
  final String? requestId;
}

abstract interface class AvaRealtimeBootstrapTransport {
  Future<AvaRealtimeBootstrapResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  });

  void close();
}

final class IoAvaRealtimeBootstrapTransport
    implements AvaRealtimeBootstrapTransport {
  IoAvaRealtimeBootstrapTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  final HttpClient _client;

  @override
  Future<AvaRealtimeBootstrapResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    final request = await _client.postUrl(endpoint).timeout(timeout);
    headers.forEach(request.headers.set);
    final bytes = utf8.encode(body);
    request.contentLength = bytes.length;
    request.add(bytes);
    final response = await request.close().timeout(timeout);
    return AvaRealtimeBootstrapResponse(
      statusCode: response.statusCode,
      contentType: response.headers.contentType?.mimeType,
      protocolVersion: response.headers.value('x-ava-protocol-version'),
      bytes: response.timeout(timeout),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

final class AvaRealtimeBootstrapService {
  const AvaRealtimeBootstrapService({
    required AvaBackendConfiguration configuration,
    required AvaRealtimeBootstrapTransport transport,
    required AvaDeviceContext deviceContext,
  })  : _configuration = configuration,
        _transport = transport,
        _deviceContext = deviceContext;

  static const int maximumResponseBytes = 64 * 1024;

  final AvaBackendConfiguration _configuration;
  final AvaRealtimeBootstrapTransport _transport;
  final AvaDeviceContext _deviceContext;

  Future<AvaSessionBootstrapV4> createSessionV4({
    required AvaRealtimeAgeBand ageBand,
    required AvaCanvasRenderProfileV4 canvasRenderProfile,
  }) async {
    final timezoneId = await _deviceContext.currentTimezoneId();
    final request = AvaSessionCreateRequestV4(
      ageBand: ageBand,
      timezoneId: timezoneId,
      canvasRenderProfile: canvasRenderProfile,
    );
    final response = await _transport.postJson(
      endpoint: _configuration.realtimeSessionsV4Endpoint,
      headers: const {
        HttpHeaders.acceptHeader: 'application/json',
        HttpHeaders.contentTypeHeader: 'application/json; charset=utf-8',
        'x-ava-protocol-version': '$avaRealtimeProtocolVersionV4',
      },
      body: jsonEncode(request.toJson()),
      timeout: _configuration.requestTimeout,
    );
    if (response.statusCode != HttpStatus.created) {
      await _readResponse(response.bytes);
      throw AvaRealtimeBootstrapException(
        message: 'Ava backend rejected protocol-v4 session creation.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.protocolVersion != '$avaRealtimeProtocolVersionV4') {
      await _readResponse(response.bytes);
      throw AvaRealtimeBootstrapException(
        message: 'Ava backend returned a mismatched protocol version.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.contentType != 'application/json') {
      await _readResponse(response.bytes);
      throw AvaRealtimeBootstrapException(
        message: 'Ava backend returned a non-JSON session bootstrap.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    final bytes = await _readResponse(response.bytes);
    final Object? decoded;
    try {
      decoded = jsonDecode(utf8.decode(bytes, allowMalformed: false));
    } on FormatException catch (error) {
      throw AvaRealtimeBootstrapException(
        message: 'Ava backend returned malformed bootstrap JSON: '
            '${error.message}',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (decoded is! Map) {
      throw const FormatException(
        'Ava protocol-v4 session bootstrap must be a JSON object.',
      );
    }
    return AvaSessionBootstrapV4.fromJson(
      decoded.cast<String, Object?>(),
      allowInsecureLoopback: _configuration.allowsInsecureLoopback,
    );
  }

  Future<Uint8List> _readResponse(Stream<List<int>> source) =>
      _readBounded(source).timeout(_configuration.requestTimeout);

  static Future<Uint8List> _readBounded(Stream<List<int>> source) async {
    final builder = BytesBuilder(copy: false);
    var length = 0;
    await for (final chunk in source) {
      length += chunk.length;
      if (length > maximumResponseBytes) {
        throw const FormatException(
          'Ava realtime bootstrap exceeded the 64 KiB response limit.',
        );
      }
      builder.add(chunk);
    }
    return builder.takeBytes();
  }
}
