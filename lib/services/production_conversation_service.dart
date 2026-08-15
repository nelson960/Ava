import 'dart:convert';
import 'dart:io';

import '../config/ava_backend_configuration.dart';
import '../models/lesson_response.dart';
import 'conversation_service.dart';

class AvaBackendResponse {
  const AvaBackendResponse({
    required this.statusCode,
    required this.contentType,
    required this.body,
    this.requestId,
  });

  final int statusCode;
  final String? contentType;
  final String body;
  final String? requestId;
}

abstract interface class AvaBackendTransport {
  Future<AvaBackendResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  });

  void close();
}

class IoAvaBackendTransport implements AvaBackendTransport {
  IoAvaBackendTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  static const _maximumResponseBytes = 256 * 1024;
  final HttpClient _client;

  @override
  Future<AvaBackendResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    final request = await _client.postUrl(endpoint).timeout(timeout);
    headers.forEach(request.headers.set);
    request.add(utf8.encode(body));
    final response = await request.close().timeout(timeout);
    final bytes = <int>[];
    await for (final chunk in response.timeout(timeout)) {
      bytes.addAll(chunk);
      if (bytes.length > _maximumResponseBytes) {
        throw const FormatException(
          'Ava backend response exceeded the 256 KiB safety limit.',
        );
      }
    }
    return AvaBackendResponse(
      statusCode: response.statusCode,
      contentType: response.headers.contentType?.mimeType,
      body: utf8.decode(bytes),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

class AvaBackendException implements Exception {
  const AvaBackendException({
    required this.message,
    this.statusCode,
    this.requestId,
  });

  final String message;
  final int? statusCode;
  final String? requestId;

  @override
  String toString() {
    final status = statusCode == null ? '' : ' (HTTP $statusCode)';
    final request = requestId == null ? '' : ' [request $requestId]';
    return '$message$status$request';
  }
}

class ProductionConversationService implements ConversationService {
  const ProductionConversationService({
    required AvaBackendConfiguration configuration,
    required AvaBackendTransport transport,
  })  : _configuration = configuration,
        _transport = transport;

  final AvaBackendConfiguration _configuration;
  final AvaBackendTransport _transport;

  @override
  Future<LessonResponse> respond(ConversationRequest request) async {
    request.validate();
    final response = await _transport.postJson(
      endpoint: _configuration.lessonTurnsEndpoint,
      headers: const {
        HttpHeaders.acceptHeader: 'application/json',
        HttpHeaders.contentTypeHeader: 'application/json; charset=utf-8',
        'x-ava-protocol-version': '1',
      },
      body: jsonEncode({
        'protocolVersion': 1,
        'request': request.toJson(),
      }),
      timeout: _configuration.requestTimeout,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AvaBackendException(
        message: 'Ava backend rejected the lesson request.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.contentType != 'application/json') {
      throw AvaBackendException(
        message: 'Ava backend returned a non-JSON response.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    final Object? decoded;
    try {
      decoded = jsonDecode(response.body);
    } on FormatException catch (error) {
      throw AvaBackendException(
        message: 'Ava backend returned malformed JSON: ${error.message}',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    final envelope = _jsonObject(decoded, 'backend response');
    _requireEnvelopeKeys(envelope);
    if (envelope['protocolVersion'] != 1) {
      throw const FormatException(
        'Ava backend protocolVersion must be exactly 1.',
      );
    }
    return LessonResponse.fromJson(
      _jsonObject(envelope['lesson'], 'backend response.lesson'),
    );
  }
}

Map<String, Object?> _jsonObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be a JSON object.');
  }
  final result = <String, Object?>{};
  for (final entry in value.entries) {
    if (entry.key is! String) {
      throw FormatException('$context contains a non-string field name.');
    }
    result[entry.key as String] = entry.value;
  }
  return result;
}

void _requireEnvelopeKeys(Map<String, Object?> envelope) {
  const required = {'protocolVersion', 'lesson'};
  final missing = required.difference(envelope.keys.toSet());
  final unknown = envelope.keys.toSet().difference(required);
  if (missing.isNotEmpty || unknown.isNotEmpty) {
    throw FormatException(
      'Ava backend response envelope mismatch; missing '
      '${missing.join(', ')}, unsupported ${unknown.join(', ')}.',
    );
  }
}
