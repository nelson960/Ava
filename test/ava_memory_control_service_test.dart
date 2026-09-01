import 'dart:convert';

import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/services/ava_device_context_service.dart';
import 'package:ava/services/ava_memory_control_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('weekly recap request uses protocol v3 and validates strict response',
      () async {
    final transport = _MemoryTransport(
      AvaMemoryControlResponse(
        statusCode: 200,
        contentType: 'application/json',
        protocolVersion: '3',
        body: jsonEncode({
          'periodStart': '2026-07-27',
          'periodEnd': '2026-08-03',
          'generatedAt': '2026-08-01T12:00:00Z',
          'resetRevision': 0,
          'records': [
            {
              'turnId': 'turn-1',
              'summary': 'Talked about the moon.',
              'occurredAt': '2026-08-01T11:00:00Z',
            },
          ],
        }),
      ),
    );
    final service = ProductionAvaMemoryControl(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/api/',
      ),
      transport: transport,
      deviceContext: const _DeviceContextFake('Asia/Kolkata'),
    );

    final recap = await service.loadWeeklyRecap();

    expect(transport.method, 'GET');
    expect(
      transport.endpoint.toString(),
      'https://ava.example/api/v3/memory/weekly-recap?timezoneId=Asia%2FKolkata',
    );
    expect(transport.headers?['x-ava-protocol-version'], '3');
    expect(recap.records.single.summary, 'Talked about the moon.');
  });

  test('reset validates receipt and never treats an error as local success',
      () async {
    final transport = _MemoryTransport(
      const AvaMemoryControlResponse(
        statusCode: 503,
        contentType: 'application/json',
        protocolVersion: null,
        body: '{"error":{"code":"memory_unavailable"}}',
        requestId: 'request-1',
      ),
    );
    final service = ProductionAvaMemoryControl(
      configuration: AvaBackendConfiguration.parse('https://ava.example/'),
      transport: transport,
      deviceContext: const _DeviceContextFake('UTC'),
    );

    await expectLater(
      service.resetConversations(),
      throwsA(
        isA<AvaMemoryControlException>()
            .having((error) => error.statusCode, 'statusCode', 503)
            .having((error) => error.requestId, 'requestId', 'request-1'),
      ),
    );
  });

  test('weekly recap rejects unknown response fields', () async {
    final transport = _MemoryTransport(
      const AvaMemoryControlResponse(
        statusCode: 200,
        contentType: 'application/json',
        protocolVersion: '3',
        body: '{"periodStart":"2026-07-27","periodEnd":"2026-08-03",'
            '"generatedAt":"2026-08-01T12:00:00Z","resetRevision":0,'
            '"records":[],"diagnosis":"none"}',
      ),
    );
    final service = ProductionAvaMemoryControl(
      configuration: AvaBackendConfiguration.parse('https://ava.example/'),
      transport: transport,
      deviceContext: const _DeviceContextFake('Europe/London'),
    );

    await expectLater(
      service.loadWeeklyRecap(),
      throwsFormatException,
    );
  });
}

final class _DeviceContextFake implements AvaDeviceContext {
  const _DeviceContextFake(this.timezoneId);

  final String timezoneId;

  @override
  Future<String> currentTimezoneId() async => timezoneId;
}

final class _MemoryTransport implements AvaMemoryControlTransport {
  _MemoryTransport(this.response);

  final AvaMemoryControlResponse response;
  String? method;
  Uri? endpoint;
  Map<String, String>? headers;

  @override
  Future<AvaMemoryControlResponse> request({
    required String method,
    required Uri endpoint,
    required Map<String, String> headers,
    required Duration timeout,
  }) async {
    this.method = method;
    this.endpoint = endpoint;
    this.headers = headers;
    return response;
  }

  @override
  void close() {}
}
