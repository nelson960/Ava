import 'dart:convert';
import 'dart:io';

import '../config/ava_backend_configuration.dart';

const _protocolVersion = '3';

final class AvaWeeklyConversationRecord {
  const AvaWeeklyConversationRecord({
    required this.turnId,
    required this.summary,
    required this.occurredAt,
  });

  final String turnId;
  final String summary;
  final DateTime occurredAt;
}

final class AvaWeeklyConversationRecap {
  const AvaWeeklyConversationRecap({
    required this.periodStart,
    required this.periodEnd,
    required this.generatedAt,
    required this.resetRevision,
    required this.records,
  });

  final DateTime periodStart;
  final DateTime periodEnd;
  final DateTime generatedAt;
  final int resetRevision;
  final List<AvaWeeklyConversationRecord> records;
}

final class AvaConversationResetReceipt {
  const AvaConversationResetReceipt({
    required this.revision,
    required this.resetAt,
  });

  final int revision;
  final DateTime resetAt;
}

abstract interface class AvaMemoryControl {
  Future<AvaWeeklyConversationRecap> loadWeeklyRecap(DateTime periodStart);

  Future<AvaConversationResetReceipt> resetConversations();
}

final class AvaMemoryControlException implements Exception {
  const AvaMemoryControlException({
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

final class AvaMemoryControlResponse {
  const AvaMemoryControlResponse({
    required this.statusCode,
    required this.contentType,
    required this.protocolVersion,
    required this.body,
    this.requestId,
  });

  final int statusCode;
  final String? contentType;
  final String? protocolVersion;
  final String body;
  final String? requestId;
}

abstract interface class AvaMemoryControlTransport {
  Future<AvaMemoryControlResponse> request({
    required String method,
    required Uri endpoint,
    required Map<String, String> headers,
    required Duration timeout,
  });

  void close();
}

final class IoAvaMemoryControlTransport implements AvaMemoryControlTransport {
  IoAvaMemoryControlTransport({HttpClient? client})
      : _client = client ?? HttpClient();

  static const _maximumResponseBytes = 64 * 1024;
  final HttpClient _client;

  @override
  Future<AvaMemoryControlResponse> request({
    required String method,
    required Uri endpoint,
    required Map<String, String> headers,
    required Duration timeout,
  }) async {
    if (method != 'GET' && method != 'DELETE') {
      throw ArgumentError.value(method, 'method', 'must be GET or DELETE');
    }
    final request = await _client.openUrl(method, endpoint).timeout(timeout);
    headers.forEach(request.headers.set);
    final response = await request.close().timeout(timeout);
    final bytes = <int>[];
    await for (final chunk in response.timeout(timeout)) {
      bytes.addAll(chunk);
      if (bytes.length > _maximumResponseBytes) {
        throw const FormatException(
          'Ava memory response exceeded the 64 KiB safety limit.',
        );
      }
    }
    return AvaMemoryControlResponse(
      statusCode: response.statusCode,
      contentType: response.headers.contentType?.mimeType,
      protocolVersion: response.headers.value('x-ava-protocol-version'),
      body: utf8.decode(bytes, allowMalformed: false),
      requestId: response.headers.value('x-request-id'),
    );
  }

  @override
  void close() => _client.close(force: true);
}

final class ProductionAvaMemoryControl implements AvaMemoryControl {
  const ProductionAvaMemoryControl({
    required AvaBackendConfiguration configuration,
    required AvaMemoryControlTransport transport,
  })  : _configuration = configuration,
        _transport = transport;

  final AvaBackendConfiguration _configuration;
  final AvaMemoryControlTransport _transport;

  @override
  Future<AvaWeeklyConversationRecap> loadWeeklyRecap(
    DateTime periodStart,
  ) async {
    final response = await _request(
      method: 'GET',
      endpoint: _configuration.weeklyRecapEndpoint(periodStart),
    );
    final payload = _jsonObject(jsonDecode(response.body), 'weekly recap');
    _requireKeys(payload, const {
      'periodStart',
      'periodEnd',
      'generatedAt',
      'resetRevision',
      'records',
    });
    final recordsValue = payload['records'];
    if (recordsValue is! List || recordsValue.length > 64) {
      throw const FormatException(
        'weekly recap records must be a list with at most 64 entries',
      );
    }
    final records = recordsValue.map((value) {
      final record = _jsonObject(value, 'weekly recap record');
      _requireKeys(record, const {'turnId', 'summary', 'occurredAt'});
      return AvaWeeklyConversationRecord(
        turnId: _boundedString(record['turnId'], 'turnId', 64),
        summary: _boundedString(record['summary'], 'summary', 240),
        occurredAt: _timestamp(record['occurredAt'], 'occurredAt'),
      );
    }).toList(growable: false);
    final start = _date(payload['periodStart'], 'periodStart');
    final end = _date(payload['periodEnd'], 'periodEnd');
    if (end.difference(start).inDays != 7) {
      throw const FormatException('weekly recap must span exactly seven days');
    }
    final revision = payload['resetRevision'];
    if (revision is! int || revision < 0) {
      throw const FormatException(
          'resetRevision must be a non-negative integer');
    }
    return AvaWeeklyConversationRecap(
      periodStart: start,
      periodEnd: end,
      generatedAt: _timestamp(payload['generatedAt'], 'generatedAt'),
      resetRevision: revision,
      records: records,
    );
  }

  @override
  Future<AvaConversationResetReceipt> resetConversations() async {
    final response = await _request(
      method: 'DELETE',
      endpoint: _configuration.resetConversationsEndpoint,
    );
    final payload = _jsonObject(jsonDecode(response.body), 'reset receipt');
    _requireKeys(payload, const {'revision', 'resetAt'});
    final revision = payload['revision'];
    if (revision is! int || revision < 1) {
      throw const FormatException('reset revision must be a positive integer');
    }
    return AvaConversationResetReceipt(
      revision: revision,
      resetAt: _timestamp(payload['resetAt'], 'resetAt'),
    );
  }

  Future<AvaMemoryControlResponse> _request({
    required String method,
    required Uri endpoint,
  }) async {
    final response = await _transport.request(
      method: method,
      endpoint: endpoint,
      headers: const {
        HttpHeaders.acceptHeader: 'application/json',
        'x-ava-protocol-version': _protocolVersion,
      },
      timeout: _configuration.requestTimeout,
    );
    if (response.statusCode != HttpStatus.ok) {
      throw AvaMemoryControlException(
        message: 'Ava backend rejected the memory request.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.protocolVersion != _protocolVersion) {
      throw AvaMemoryControlException(
        message: 'Ava backend returned a mismatched protocol version.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    if (response.contentType != 'application/json') {
      throw AvaMemoryControlException(
        message: 'Ava backend returned a non-JSON memory response.',
        statusCode: response.statusCode,
        requestId: response.requestId,
      );
    }
    return response;
  }
}

Map<String, Object?> _jsonObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be a JSON object');
  }
  return value.cast<String, Object?>();
}

void _requireKeys(Map<String, Object?> value, Set<String> expected) {
  if (value.keys.toSet().difference(expected).isNotEmpty ||
      expected.difference(value.keys.toSet()).isNotEmpty) {
    throw const FormatException('Ava memory response has unexpected fields');
  }
}

String _boundedString(Object? value, String field, int maximum) {
  if (value is! String ||
      value.trim() != value ||
      value.isEmpty ||
      value.length > maximum) {
    throw FormatException('$field must be a bounded non-empty string');
  }
  return value;
}

DateTime _date(Object? value, String field) {
  final raw = _boundedString(value, field, 10);
  final parsed = DateTime.tryParse(raw);
  if (parsed == null ||
      raw.length != 10 ||
      parsed.toIso8601String().substring(0, 10) != raw) {
    throw FormatException('$field must be an ISO calendar date');
  }
  return DateTime(parsed.year, parsed.month, parsed.day);
}

DateTime _timestamp(Object? value, String field) {
  final raw = _boundedString(value, field, 40);
  final parsed = DateTime.tryParse(raw);
  if (parsed == null || !parsed.isUtc) {
    throw FormatException('$field must be an ISO UTC timestamp');
  }
  return parsed;
}
