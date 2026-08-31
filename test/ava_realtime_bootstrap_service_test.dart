import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/services/ava_realtime_bootstrap_service.dart';
import 'package:ava/services/ava_device_context_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('creates a strict v4 session without sending profile identity',
      () async {
    final bootstrapJson =
        File('protocol/fixtures/ava_session_bootstrap_v4.json')
            .readAsStringSync();
    final transport = _BootstrapTransportFake(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '4',
        bytes: Stream.value(utf8.encode(bootstrapJson)),
        requestId: 'request-bootstrap-1',
      ),
    );
    final service = AvaRealtimeBootstrapService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/api/',
      ),
      transport: transport,
      deviceContext: const _DeviceContextFake('Asia/Kolkata'),
    );

    final bootstrap = await service.createSessionV4(
      ageBand: AvaRealtimeAgeBand.middleChildhood,
      canvasRenderProfile: _profile,
    );

    expect(bootstrap.sessionId, 'session-1234');
    expect(
      bootstrap.webrtcUrl,
      'https://ava.example.test/v4/realtime/sessions/session-1234/webrtc',
    );
    expect(
      transport.endpoint,
      Uri.parse('https://ava.example/api/v4/realtime/sessions'),
    );
    expect(transport.headers['x-ava-protocol-version'], '4');
    expect(transport.headers[HttpHeaders.acceptHeader], 'application/json');
    expect(
      jsonDecode(transport.body),
      {
        'protocolVersion': 4,
        'ageBand': 'middleChildhood',
        'timezoneId': 'Asia/Kolkata',
        'canvasRenderProfile': _profile.toJson(),
      },
    );
    expect(transport.body, isNot(contains('name')));
    expect(transport.body, isNot(contains('dateOfBirth')));
    final profile = (jsonDecode(transport.body)
        as Map<String, Object?>)['canvasRenderProfile'] as Map;
    expect(profile['orientation'], 'landscape');
    expect(profile['widthClass'], 'regular');
    expect(profile['heightClass'], 'compact');
    expect(profile['touchEnvironment'], 'direct');
    expect(profile['minimumInteractionTargetPoints'], 44.0);
    expect((profile['safeInsets'] as Map)['bottom'], closeTo(0.04, 0.000001));
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
        deviceContext: const _DeviceContextFake('UTC'),
      );
      await expectLater(
        service.createSessionV4(
          ageBand: AvaRealtimeAgeBand.unknown,
          canvasRenderProfile: _profile,
        ),
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
        protocolVersion: '3',
        bytes: Stream.value(utf8.encode('{}')),
      ),
      isA<AvaRealtimeBootstrapException>(),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'text/html',
        protocolVersion: '4',
        bytes: Stream.value(utf8.encode('<html></html>')),
      ),
      isA<AvaRealtimeBootstrapException>(),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '4',
        bytes: Stream.value(utf8.encode('{')),
      ),
      isA<AvaRealtimeBootstrapException>(),
    );
    await expectFailure(
      AvaRealtimeBootstrapResponse(
        statusCode: HttpStatus.created,
        contentType: 'application/json',
        protocolVersion: '4',
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
          protocolVersion: '4',
          bytes: body.stream,
        ),
      ),
      deviceContext: const _DeviceContextFake('Europe/London'),
    );

    await expectLater(
      service.createSessionV4(
        ageBand: AvaRealtimeAgeBand.unknown,
        canvasRenderProfile: _profile,
      ),
      throwsA(isA<TimeoutException>()),
    );
    await body.close();
  });

  test('Apple device-context bridge returns the native timezone identifier',
      () async {
    const channel = MethodChannel('test/ava_device_context');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async {
      expect(call.method, 'currentTimezoneId');
      return 'America/New_York';
    });
    addTearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, null);
    });

    final service = AvaDeviceContextService(channel: channel);
    expect(await service.currentTimezoneId(), 'America/New_York');
  });
}

final _profile = AvaCanvasRenderProfileV4.measure(
  workspaceWidthPoints: 800,
  workspaceHeightPoints: 500,
  safeTopPoints: 0,
  safeRightPoints: 0,
  safeBottomPoints: 20,
  safeLeftPoints: 0,
  directTouch: true,
  textScaleFactor: 1,
);

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

final class _DeviceContextFake implements AvaDeviceContext {
  const _DeviceContextFake(this.timezoneId);

  final String timezoneId;

  @override
  Future<String> currentTimezoneId() async => timezoneId;
}
