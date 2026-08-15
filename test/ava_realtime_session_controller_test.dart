import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_canvas_engine.dart';
import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/services/ava_realtime_bootstrap_service.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_runtime_factory.dart';
import 'package:ava/services/ava_realtime_session.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:ava/state/ava_realtime_session_controller.dart';
import 'package:ava/state/ava_session_state.dart';
import 'package:flutter_test/flutter_test.dart';

const _sessionId = 'session-persistent-controller';
const _turnId = 'turn-persistent-controller';
const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava persistent controller test\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('startup prepares once and mic taps only toggle the existing track',
      () async {
    final harness = _ControllerHarness();
    addTearDown(harness.close);

    final preparing = harness.controller.prepare();
    await _waitFor(
      () => harness.transport.sentEvents.isNotEmpty,
      'clientReady was not sent',
    );
    harness.sendListeningTurn();
    await preparing;

    expect(harness.bootstrap.requestCount, 1);
    expect(harness.media.microphoneEnabled, isFalse);
    expect(harness.controller.state.turnState, AvaSessionPhase.idle);
    expect(harness.controller.state.userMicIntent, isFalse);
    expect(harness.controller.state.actualTrackEnabled, isFalse);

    await harness.controller.setMicrophoneActive(true);
    expect(harness.media.microphoneEnabled, isTrue);
    expect(harness.controller.state.userMicIntent, isTrue);
    expect(harness.controller.state.actualTrackEnabled, isTrue);
    expect(harness.bootstrap.requestCount, 1);

    harness.presentation.setPhase(AvaRealtimeTurnPhase.planning);
    expect(harness.controller.state.turnState, AvaSessionPhase.thinking);
    expect(harness.controller.state.userMicIntent, isTrue);
    harness.presentation.setPhase(AvaRealtimeTurnPhase.speaking);
    expect(harness.controller.state.turnState, AvaSessionPhase.speaking);
    expect(harness.controller.state.userMicIntent, isTrue);
    harness.presentation.complete(
      AvaRealtimeServerEvent.fromJson({
        'protocolVersion': 3,
        'event': 'turnCancelled',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 3,
        'revision': 3,
        'deliveredSampleCount': 0,
        'deliveredText': '',
        'reason': 'testTurnBoundary',
      }),
    );
    expect(harness.controller.state.turnState, AvaSessionPhase.listening);
    expect(harness.controller.state.userMicIntent, isTrue);
    expect(harness.controller.state.actualTrackEnabled, isTrue);

    await harness.controller.setMicrophoneActive(false);
    expect(harness.media.microphoneEnabled, isFalse);
    expect(harness.media.closed, isFalse);
    expect(harness.controller.state.userMicIntent, isFalse);
    expect(harness.controller.state.actualTrackEnabled, isFalse);
    expect(harness.bootstrap.requestCount, 1);

    await harness.controller.setMicrophoneActive(true);
    expect(harness.media.microphoneEnabled, isTrue);
    expect(harness.bootstrap.requestCount, 1);
    expect(
      harness.transport.sentEvents
          .where(
            (event) => event.type == AvaRealtimeClientEventType.clientReady,
          )
          .length,
      1,
    );
  });

  test('rapid on then off during startup never revives stale mic intent',
      () async {
    final harness = _ControllerHarness();
    addTearDown(harness.close);

    final turnOn = harness.controller.setMicrophoneActive(true);
    expect(harness.controller.state.userMicIntent, isTrue);
    await _waitFor(
      () => harness.transport.sentEvents.isNotEmpty,
      'clientReady was not sent',
    );

    final turnOff = harness.controller.setMicrophoneActive(false);
    expect(harness.controller.state.userMicIntent, isFalse);
    harness.sendListeningTurn();
    await Future.wait([turnOn, turnOff]);

    expect(harness.media.microphoneEnabled, isFalse);
    expect(harness.bootstrap.requestCount, 1);

    await harness.controller.setMicrophoneActive(true);
    expect(harness.media.microphoneEnabled, isTrue);
    expect(harness.bootstrap.requestCount, 1);
  });

  test('a failed turn never changes mic intent or the live track', () async {
    final harness = _ControllerHarness();
    addTearDown(harness.close);

    final preparing = harness.controller.prepare();
    await _waitFor(
      () => harness.transport.sentEvents.isNotEmpty,
      'clientReady was not sent',
    );
    harness.sendListeningTurn();
    await preparing;
    await harness.controller.setMicrophoneActive(true);

    harness.presentation.setPhase(AvaRealtimeTurnPhase.planning);
    harness.presentation.complete(
      AvaRealtimeServerEvent.fromJson({
        'protocolVersion': 3,
        'event': 'turnFailed',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 3,
        'revision': 3,
        'failure': {
          'failureId': 'failure-controller-model',
          'component': 'realtimeProvider',
          'code': 'modelInvalid',
          'message': 'The response failed validation.',
          'retryable': false,
        },
        'deliveredSampleCount': 0,
        'deliveredText': '',
      }),
    );

    expect(harness.controller.state.turnState, AvaSessionPhase.failure);
    expect(harness.controller.state.userMicIntent, isTrue);
    expect(harness.controller.state.actualTrackEnabled, isTrue);
    expect(harness.media.microphoneEnabled, isTrue);
  });

  test('canvas and memory failures remain isolated from conversation media',
      () async {
    final harness = _ControllerHarness();
    addTearDown(harness.close);

    final preparing = harness.controller.prepare();
    await _waitFor(
      () => harness.transport.sentEvents.isNotEmpty,
      'clientReady was not sent',
    );
    harness.sendListeningTurn();
    await preparing;
    await harness.controller.setMicrophoneActive(true);

    harness.presentation.setPhase(AvaRealtimeTurnPhase.synthesizing);
    harness.presentation.recordCanvasRejection(
      'The optional visual could not be prepared.',
    );
    expect(
      harness.controller.state.canvasState,
      AvaCanvasSessionState.rejected,
    );
    expect(harness.controller.state.userMicIntent, isTrue);
    expect(harness.controller.state.actualTrackEnabled, isTrue);

    harness.presentation.setPhase(AvaRealtimeTurnPhase.speaking);
    harness.presentation.recordMemoryPersistenceFailure(
      const AvaRealtimeFailure(
        failureId: 'failure-controller-memory',
        component: 'memoryStore',
        code: 'memoryUnavailable',
        message: 'Delivered turn could not be persisted.',
        retryable: true,
      ),
    );
    harness.presentation.complete(
      AvaRealtimeServerEvent.fromJson({
        'protocolVersion': 3,
        'event': 'turnCompleted',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 3,
        'revision': 3,
        'deliveredSampleCount': 0,
        'deliveredText': '',
      }),
    );

    expect(
      harness.controller.state.memoryState,
      AvaMemorySessionState.retryRequired,
    );
    expect(harness.controller.state.turnState, AvaSessionPhase.listening);
    expect(harness.controller.state.userMicIntent, isTrue);
    expect(harness.controller.state.actualTrackEnabled, isTrue);
    expect(harness.media.microphoneEnabled, isTrue);
  });
}

final class _ControllerHarness {
  _ControllerHarness() {
    bootstrap = _BootstrapTransportFake();
    media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp);
    transport = _PushRealtimeTransport();
    presentation = AvaRealtimePresentationController();
    canvasEngine = AvaCanvasEngine();
    factory = AvaRealtimeRuntimeFactory(
      bootstrapService: AvaRealtimeBootstrapService(
        configuration: AvaBackendConfiguration.parse(
          'https://ava.example.test',
        ),
        transport: bootstrap,
      ),
      mediaConnector: (bootstrap) async => media,
      transportConnector: (bootstrap) async => transport,
      speechOutput: const _NoopPcmOutput(),
      canvasEngine: canvasEngine,
      presentation: presentation,
      cancelRequestIdFactory: () => 'cancel-controller-test',
    );
    controller = AvaRealtimeSessionController(
      runtimeFactory: factory,
      presentation: presentation,
      canvasEngine: canvasEngine,
      ageBand: () => AvaRealtimeAgeBand.middleChildhood,
      volume: () => 0.8,
    );
  }

  late final _BootstrapTransportFake bootstrap;
  late final DeterministicAvaRealtimeMediaSession media;
  late final _PushRealtimeTransport transport;
  late final AvaRealtimePresentationController presentation;
  late final AvaCanvasEngine canvasEngine;
  late final AvaRealtimeRuntimeFactory factory;
  late final AvaRealtimeSessionController controller;

  void sendListeningTurn() {
    for (final payload in const [
      {
        'protocolVersion': 3,
        'event': 'turnCreated',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 0,
        'revision': 0,
        'requestId': 'request-persistent-controller',
      },
      {
        'protocolVersion': 3,
        'event': 'turnPhaseChanged',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 1,
        'revision': 1,
        'phase': 'connecting',
      },
      {
        'protocolVersion': 3,
        'event': 'turnPhaseChanged',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 2,
        'revision': 2,
        'phase': 'listening',
      },
    ]) {
      transport.add(
        AvaRealtimeControlFrame(
          AvaRealtimeServerEvent.fromJson(payload),
        ),
      );
    }
  }

  Future<void> close() async {
    controller.dispose();
    await _waitFor(() => media.closed, 'media did not close on disposal');
    await factory.close();
  }
}

final class _BootstrapTransportFake implements AvaRealtimeBootstrapTransport {
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
      'sessionId': _sessionId,
      'eventUrl': 'wss://ava.example.test/v3/realtime/$_sessionId/events',
      'webrtcUrl': 'https://ava.example.test/v3/realtime/$_sessionId/webrtc',
      'sessionToken': 'session-token-controller-test',
      'webrtcToken': 'webrtc-token-controller-test',
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

final class _PushRealtimeTransport implements AvaRealtimeTransport {
  final StreamController<AvaRealtimeInboundFrame> _frames = StreamController();
  final List<AvaRealtimeClientEvent> sentEvents = [];
  bool _closed = false;

  void add(AvaRealtimeInboundFrame frame) => _frames.add(frame);

  @override
  Stream<AvaRealtimeInboundFrame> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEvent event) async {
    if (_closed) throw StateError('transport is closed');
    sentEvents.add(event);
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _frames.close();
  }
}

final class _NoopPcmOutput implements PcmSpeechOutputService {
  const _NoopPcmOutput();

  @override
  Future<void> append(
      {required String turnId, required Uint8List audio}) async {}

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async =>
      const PcmStopResult(renderedSampleCount: 0);

  @override
  Future<PcmStopResult> finish({required String turnId}) async =>
      const PcmStopResult(renderedSampleCount: 0);

  @override
  Future<Duration> position({required String turnId}) async => Duration.zero;

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async =>
      const PcmPlaybackProgress(
        renderedSampleCount: 0,
        scheduledSampleCount: 0,
        sampleRateHz: 24000,
      );

  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {}

  @override
  Future<void> stop() async {}
}

Future<void> _waitFor(bool Function() predicate, String failure) async {
  for (var attempt = 0; attempt < 200; attempt += 1) {
    if (predicate()) return;
    await Future<void>.delayed(const Duration(milliseconds: 1));
  }
  fail(failure);
}
