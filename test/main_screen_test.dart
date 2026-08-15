import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ava/canvas/ava_canvas_engine.dart';
import 'package:ava/canvas/ava_canvas_geometry.dart';
import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_canvas_protocol.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/services/ava_realtime_bootstrap_service.dart';
import 'package:ava/services/ava_realtime_session.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/memory_service.dart';
import 'package:ava/state/app_state.dart';
import 'package:ava/ui/avatar/ava_filament_viewer.dart';
import 'package:ava/ui/dashboard/ava_canvas_view.dart';
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
          avaRealtimeMediaConnectorProvider.overrideWithValue(
            (bootstrap) async => DeterministicAvaRealtimeMediaSession(
              offerSdp: _testOfferSdp,
            ),
          ),
          avaRealtimeTransportConnectorProvider.overrideWithValue(
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

  testWidgets(
    'dynamic canvas stays in the lower half and leaves avatar input exposed',
    (tester) async {
      final database = AppDatabase.forTesting(NativeDatabase.memory());
      addTearDown(database.close);
      final engine = AvaCanvasEngine();
      final gallery = jsonDecode(
        File(
          'protocol/fixtures/ava_canvas_gallery_v3.json',
        ).readAsStringSync(),
      ) as List;
      final interactive =
          (gallery.last as Map)['transaction'] as Map<String, Object?>;
      final applied = engine.apply(
        AvaCanvasTransaction.fromJson(interactive),
      );
      expect(applied.accepted, isTrue);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            avaCanvasEngineProvider.overrideWith((ref) => engine),
            databaseProvider.overrideWithValue(database),
          ],
          child: const MaterialApp(home: MainScreen()),
        ),
      );
      await tester.pump(const Duration(milliseconds: 500));

      final canvasFinder = find.byType(AvaCanvasView);
      expect(canvasFinder, findsOneWidget);
      expect(find.byType(AvaFilamentViewer), findsOneWidget);
      final screenSize =
          tester.view.physicalSize / tester.view.devicePixelRatio;
      final canvasRect = tester.getRect(canvasFinder);
      expect(canvasRect.top, greaterThanOrEqualTo(screenSize.height * 0.5));

      final beforeTopTouch = engine.scene!.interactionRevision;
      await tester
          .tapAt(Offset(screenSize.width / 2, screenSize.height * 0.25));
      await tester.pump();
      expect(engine.scene!.interactionRevision, beforeTopTouch);

      final viewport = AvaCanvasViewportTransform.forSize(canvasRect.size);
      await tester.tapAt(
        canvasRect.topLeft + viewport.toLocal(const Offset(500, 465)),
      );
      await tester.pump();
      expect(
        engine.effectiveNodeJson(
          engine.scene!.nodes['interactive_answer']!,
        )['visible'],
        isTrue,
      );

      await tester.tap(find.byKey(const ValueKey('close_dynamic_canvas')));
      await tester.pump(const Duration(milliseconds: 500));
      expect(engine.panelVisible, isFalse);
      expect(find.byType(AvaFilamentViewer), findsOneWidget);
    },
  );
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
      'protocolVersion': 3,
      'sessionId': 'session-main-screen-prepared',
      'eventUrl':
          'wss://ava.example.test/v3/realtime/session-main-screen-prepared/events',
      'webrtcUrl':
          'https://ava.example.test/v3/realtime/session-main-screen-prepared/webrtc',
      'sessionToken': 'session-token-main-screen',
      'webrtcToken': 'webrtc-token-main-screen',
      'expiresAt': DateTime.now()
          .toUtc()
          .add(const Duration(minutes: 2))
          .toIso8601String(),
      'limits': {
        'maxTurnSeconds': 90,
        'maxResponseCharacters': 1800,
        'maxCanvasNodes': 96,
        'maxAudioChunkBytes': 192000,
        'playbackProgressIntervalMs': 250,
      },
    };
    return AvaRealtimeBootstrapResponse(
      statusCode: HttpStatus.created,
      contentType: 'application/json',
      protocolVersion: '3',
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

final class _HoldingRealtimeTransport implements AvaRealtimeTransport {
  final StreamController<AvaRealtimeInboundFrame> _frames = StreamController();
  bool _closed = false;

  @override
  Stream<AvaRealtimeInboundFrame> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEvent event) async {}

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _frames.close();
  }
}
