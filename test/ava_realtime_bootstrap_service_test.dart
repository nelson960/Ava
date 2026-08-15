import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/services/ava_realtime_bootstrap_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('creates a strict v3 session without sending profile identity',
      () async {
    final bootstrapJson =
        File('protocol/fixtures/ava_session_bootstrap_v3.json')
            .readAsStringSync();
    final transport = _BootstrapTransportFake(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '3',
        bytes: Stream.value(utf8.encode(bootstrapJson)),
        requestId: 'request-bootstrap-1',
      ),
    );
    final service = AvaRealtimeBootstrapService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/api/',
      ),
      transport: transport,
    );

    final bootstrap = await service.createSession(
      ageBand: AvaRealtimeAgeBand.middleChildhood,
    );

    expect(bootstrap.sessionId, 'session-1234');
    expect(
      bootstrap.webrtcUrl,
      'https://ava.example.test/v3/realtime/sessions/session-1234/webrtc',
    );
    expect(
      transport.endpoint,
      Uri.parse('https://ava.example/api/v3/realtime/sessions'),
    );
    expect(transport.headers['x-ava-protocol-version'], '3');
    expect(transport.headers[HttpHeaders.acceptHeader], 'application/json');
    expect(
      jsonDecode(transport.body),
      {
        'protocolVersion': 3,
        'ageBand': 'middleChildhood',
      },
    );
    expect(transport.body, isNot(contains('name')));
    expect(transport.body, isNot(contains('dateOfBirth')));
  });

  test('rejects status, content type, malformed JSON and oversized bodies',
      () async {
    Future<void> expectFailure(
      AvaRealtimeBootstrapResponse response,
      Matcher matcher,
    ) async {
      final service = AvaRealtimeBootstrapService(
        configuration: AvaBackendConfiguration.parse('https://ava.example/'),
        transport: _BootstrapTransportFake(response),
      );
      await expectLater(
        service.createSession(ageBand: AvaRealtimeAgeBand.unknown),
        throwsA(matcher),
      );
    }

    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.serviceUnavailable,
        contentType: 'application/json',
        protocolVersion: null,
        bytes: Stream.value(utf8.encode('{"private":"details"}')),
        requestId: 'request-down',
      ),
      isA<AvaRealtimeBootstrapException>()
          .having((error) => error.statusCode, 'statusCode', 503)
          .having((error) => error.requestId, 'requestId', 'request-down'),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '2',
        bytes: Stream.value(utf8.encode('{}')),
      ),
      isA<AvaRealtimeBootstrapException>(),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'text/html',
        protocolVersion: '3',
        bytes: Stream.value(utf8.encode('<html></html>')),
      ),
      isA<AvaRealtimeBootstrapException>(),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '3',
        bytes: Stream.value(utf8.encode('{')),
      ),
      isA<AvaRealtimeBootstrapException>(),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '3',
        bytes: Stream.value(
          List<int>.filled(
            AvaRealtimeBootstrapService.maximumResponseBytes + 1,
            0,
          ),
        ),
      ),
      isA<FormatException>(),
    );
  });

  test('applies one total deadline to a stalled bootstrap body', () async {
    final body = StreamController<List<int>>();
    final service = AvaRealtimeBootstrapService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/',
        requestTimeout: const Duration(milliseconds: 20),
      ),
      transport: _BootstrapTransportFake(
        AvaRealtimeBootstrapResponse(
          statusCode: HttpStatus.created,
          contentType: 'application/json',
          protocolVersion: '3',
          bytes: body.stream,
        ),
      ),
    );

    await expectLater(
      service.createSession(ageBand: AvaRealtimeAgeBand.unknown),
      throwsA(isA<TimeoutException>()),
    );
    await body.close();
  });
}

final class _BootstrapTransportFake implements AvaRealtimeBootstrapTransport {
  _BootstrapTransportFake(this.response);

  final AvaRealtimeBootstrapResponse response;
  late Uri endpoint;
  late Map<String, String> headers;
  late String body;
  bool closed = false;

  @override
  Future<AvaRealtimeBootstrapResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    this.endpoint = endpoint;
    this.headers = Map.unmodifiable(headers);
    this.body = body;
    return response;
  }

  @override
  void close() {
    closed = true;
  }
}
