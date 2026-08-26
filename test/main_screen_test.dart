import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/services/ava_realtime_bootstrap_service.dart';
import 'package:ava/services/ava_realtime_session_v4.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/memory_service.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/ui/avatar/ava_filament_viewer.dart';
import 'package:ava/ui/main_screen.dart';
import 'package:drift/native.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('main screen starts its multi-controller living world', (
    tester,
  ) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [databaseProvider.overrideWithValue(database)],
        child: const MaterialApp(home: MainScreen()),
      ),
    );
    await tester.pump();

    expect(tester.takeException(), isNull);
    expect(find.byType(MainScreen), findsOneWidget);
    expect(find.byIcon(CupertinoIcons.mic_slash_fill), findsOneWidget);
    expect(find.bySemanticsLabel('Turn microphone on'), findsOneWidget);
    final avatarViewer = tester.widget<AvaFilamentViewer>(
      find.byType(AvaFilamentViewer),
    );
    expect(avatarViewer.gestureConfiguration.frontPolarAngleDegrees, 77);
    expect(avatarViewer.gestureConfiguration.minimumPolarAngleDegrees, 66);
    expect(avatarViewer.gestureConfiguration.maximumPolarAngleDegrees, 92);
  });

  testWidgets('configured main screen starts persistent preparation', (
    tester,
  ) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    final bootstrapTransport = _CountingBootstrapTransport();
    final realtimeTransport = _HoldingRealtimeTransport();
    addTearDown(database.close);
    addTearDown(bootstrapTransport.close);
    addTearDown(realtimeTransport.close);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          databaseProvider.overrideWithValue(database),
          avaBackendConfigurationProvider.overrideWithValue(
            AvaBackendConfiguration.parse('https://ava.example.test/'),
          ),
          avaRealtimeBootstrapTransportProvider.overrideWithValue(
            bootstrapTransport,
          ),
          avaRealtimeMediaConnectorV4Provider.overrideWithValue(
            (bootstrap) async => DeterministicAvaRealtimeMediaSession(
              offerSdp: _testOfferSdp,
            ),
          ),
          avaRealtimeRawTransportConnectorV4Provider.overrideWithValue(
            (bootstrap) async => realtimeTransport,
          ),
        ],
        child: const MaterialApp(home: MainScreen()),
      ),
    );
    await tester.pump();

    expect(bootstrapTransport.requestCount, 1);
    expect(find.byIcon(CupertinoIcons.mic_slash_fill), findsOneWidget);
  });

  testWidgets('rapid recurring session phases keep unique transition keys', (
    tester,
  ) async {
    final database = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(database.close);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [databaseProvider.overrideWithValue(database)],
        child: const MaterialApp(home: MainScreen()),
      ),
    );
    await tester.pump();

    final container = ProviderScope.containerOf(
      tester.element(find.byType(MainScreen)),
    );
    final presentation = container.read(
      avaRealtimePresentationProvider.notifier,
    );
    presentation.beginTurn(
      sessionId: 'session-main-screen-transition',
      turnId: 'turn-main-screen-transition',
    );
    presentation.setPhase(AvaRealtimeTurnPhase.planning);
    await tester.pump();
    presentation.setPhase(AvaRealtimeTurnPhase.speaking);
    await tester.pump(const Duration(milliseconds: 20));
    presentation.setPhase(AvaRealtimeTurnPhase.synthesizing);
    await tester.pump();

    expect(tester.takeException(), isNull);
    expect(find.text('Thinking'), findsWidgets);
    await tester.pump(const Duration(milliseconds: 200));
    expect(tester.takeException(), isNull);
    expect(find.text('Thinking'), findsOneWidget);
  });
}

final class _CountingBootstrapTransport
    implements AvaRealtimeBootstrapTransport {
  int requestCount = 0;

  @override
  Future<AvaRealtimeBootstrapResponse> postJson({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    requestCount += 1;
    final payload = <String, Object?>{
      'protocolVersion': 4,
      'sessionId': 'session-main-screen-prepared',
      'eventUrl':
          'wss://ava.example.test/v4/realtime/sessions/session-main-screen-prepared/events',
      'webrtcUrl':
          'https://ava.example.test/v4/realtime/sessions/session-main-screen-prepared/webrtc',
      'canvasDocumentBaseUrl':
          'https://ava.example.test/v4/realtime/sessions/session-main-screen-prepared/canvas-documents',
      'sessionToken': 'session-token-main-screen',
      'webrtcToken': 'webrtc-token-main-screen',
      'canvasToken': 'canvas-token-main-screen',
      'expiresAt': DateTime.now()
          .toUtc()
          .add(const Duration(minutes: 2))
          .toIso8601String(),
      'limits': {
        'maxTurnSeconds': 90,
        'maxResponseCharacters': 1800,
        'maxSvgBytes': 262144,
        'maxManifestBytes': 65536,
        'maxAudioChunkBytes': 192000,
        'playbackProgressIntervalMs': 250,
      },
      'rendererCapabilityVersion': 'ava-svg-renderer-v2',
      'validatorVersion': 'ava-svg-validator-v2',
      'manifestVersion': 'ava-svg-manifest-v2',
    };
    return AvaRealtimeBootstrapResponse(
      statusCode: HttpStatus.created,
      contentType: 'application/json',
      protocolVersion: '4',
      bytes: Stream.value(utf8.encode(jsonEncode(payload))),
    );
  }

  @override
  void close() {}
}

const _testOfferSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava main screen test\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

final class _HoldingRealtimeTransport implements AvaRealtimeRawTransportV4 {
  final StreamController<AvaRealtimeInboundFrameV4> _frames =
      StreamController();
  bool _closed = false;

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {}

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _frames.close();
  }
}
