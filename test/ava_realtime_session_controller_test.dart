import 'dart:async';
import 'dart:typed_data';

import 'package:ava/canvas/ava_svg_canvas_session_host.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_runtime_factory.dart';
import 'package:ava/services/ava_realtime_session_v4.dart';
import 'package:ava/services/ava_realtime_turn_runtime.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:ava/state/ava_realtime_session_controller.dart';
import 'package:ava/state/ava_session_state.dart';
import 'package:flutter_test/flutter_test.dart';

const _sessionId = 'session-controller-v4';
const _turnId = 'turn-controller-v4';
const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava persistent controller test\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('startup prepares once and mic taps retain the same V4 session',
      () async {
    final harness = _ControllerHarness();
    addTearDown(harness.close);

    final preparing = harness.controller.prepare();
    await harness.raw.waitForSent('clientReady');
    harness.sendListeningTurn();
    await preparing;

    expect(harness.builder.createCount, 1);
    expect(harness.media.microphoneEnabled, isFalse);
    expect(harness.controller.state.connectionHealth,
        AvaConnectionHealth.connected);

    await harness.controller.setMicrophoneActive(true);
    expect(harness.media.microphoneEnabled, isTrue);
    await harness.controller.setMicrophoneActive(false);
    expect(harness.media.microphoneEnabled, isFalse);
    expect(harness.media.closed, isFalse);
    await harness.controller.setMicrophoneActive(true);

    expect(harness.media.microphoneEnabled, isTrue);
    expect(harness.builder.createCount, 1);
    expect(
      harness.raw.sentJson.where((event) => event['event'] == 'clientReady'),
      hasLength(1),
    );
  });

  test('rapid on then off during startup never revives stale mic intent',
      () async {
    final harness = _ControllerHarness();
    addTearDown(harness.close);

    final turnOn = harness.controller.setMicrophoneActive(true);
    await harness.raw.waitForSent('clientReady');
    final turnOff = harness.controller.setMicrophoneActive(false);
    harness.sendListeningTurn();
    await Future.wait([turnOn, turnOff]);

    expect(harness.controller.state.userMicIntent, isFalse);
    expect(harness.media.microphoneEnabled, isFalse);
    expect(harness.builder.createCount, 1);
  });

  test('muted warm expiry cancels and closes without a failure state',
      () async {
    final harness = _ControllerHarness(
      warmRetention: const Duration(milliseconds: 20),
    );
    addTearDown(harness.close);
    final preparing = harness.controller.prepare();
    await harness.raw.waitForSent('clientReady');
    harness.sendListeningTurn();
    await preparing;
    await harness.controller.setMicrophoneActive(true);
    await harness.controller.setMicrophoneActive(false);

    await harness.raw.waitForSent('cancelTurn');
    harness.addControl({
      'protocolVersion': 4,
      'event': 'turnPhaseChanged',
      'sessionId': _sessionId,
      'turnId': _turnId,
      'sequence': 3,
      'revision': 3,
      'phase': 'cancelling',
    });
    harness.addControl({
      'protocolVersion': 4,
      'event': 'turnCancelled',
      'sessionId': _sessionId,
      'turnId': _turnId,
      'sequence': 4,
      'revision': 4,
      'deliveredSampleCount': 0,
      'deliveredText': '',
      'reason': 'mutedConversationExpired',
    });
    await harness.mediaClosed.future;

    expect(harness.controller.state.connectionHealth,
        AvaConnectionHealth.disconnected);
    expect(harness.controller.state.turnState, AvaSessionPhase.idle);
    expect(harness.controller.state.errorMessage, isNull);
  });
}

final class _ControllerHarness {
  _ControllerHarness({
    Duration warmRetention = const Duration(minutes: 2),
  })  : raw = _PushRawTransportV4(),
        media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp),
        presentation = AvaRealtimePresentationController(),
        canvasHost = AvaSvgCanvasSessionHost() {
    builder = _RuntimeBuilder(
      raw: raw,
      media: media,
      presentation: presentation,
      canvasHost: canvasHost,
    );
    controller = AvaRealtimeSessionController(
      runtimeFactory: builder,
      presentation: presentation,
      canvasHost: canvasHost,
      ageBand: () => AvaRealtimeAgeBand.middleChildhood,
      volume: () => 0.8,
      warmRetention: warmRetention,
    );
    unawaited(media.states.drain<void>().whenComplete(() {
      if (!mediaClosed.isCompleted) mediaClosed.complete();
    }));
  }

  final _PushRawTransportV4 raw;
  final DeterministicAvaRealtimeMediaSession media;
  final AvaRealtimePresentationController presentation;
  final AvaSvgCanvasSessionHost canvasHost;
  final Completer<void> mediaClosed = Completer<void>();
  late final _RuntimeBuilder builder;
  late final AvaRealtimeSessionController controller;
  bool _closed = false;

  void sendListeningTurn() {
    for (final event in [
      {
        'protocolVersion': 4,
        'event': 'turnCreated',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 0,
        'revision': 0,
        'requestId': 'request-controller-v4',
      },
      for (final phase in const [
        (1, 1, 'connecting'),
        (2, 2, 'listening'),
      ])
        {
          'protocolVersion': 4,
          'event': 'turnPhaseChanged',
          'sessionId': _sessionId,
          'turnId': _turnId,
          'sequence': phase.$1,
          'revision': phase.$2,
          'phase': phase.$3,
        },
    ]) {
      addControl(event);
    }
  }

  void addControl(Map<String, Object?> json) {
    raw.add(
      AvaRealtimeControlFrameV4(AvaRealtimeServerEventV4.fromJson(json)),
    );
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    controller.dispose();
    await builder.close();
    canvasHost.dispose();
  }
}

final class _RuntimeBuilder implements AvaRealtimeRuntimeBuilder {
  _RuntimeBuilder({
    required this.raw,
    required this.media,
    required this.presentation,
    required this.canvasHost,
  });

  final _PushRawTransportV4 raw;
  final DeterministicAvaRealtimeMediaSession media;
  final AvaRealtimePresentationController presentation;
  final AvaSvgCanvasSessionHost canvasHost;
  AvaRealtimeTurnRuntime? runtime;
  int createCount = 0;

  @override
  Future<AvaRealtimeTurnRuntime> create({
    required AvaRealtimeAgeBand ageBand,
    required double volume,
    void Function(bool enabled)? onMicrophoneTrackChanged,
  }) async {
    createCount += 1;
    final created = AvaRealtimeTurnRuntime(
      sessionId: _sessionId,
      limits: const AvaSessionLimitsV4(
        maxTurnSeconds: 5,
        maxResponseCharacters: 1200,
        maxSvgBytes: 262144,
        maxManifestBytes: 65536,
        maxAudioChunkBytes: 192000,
        playbackProgressIntervalMs: 100,
      ),
      mediaSession: media,
      transport: raw,
      documents: const _UnexpectedDocumentLoader(),
      speechOutput: const _NoopPcmOutput(),
      presentation: presentation,
      volume: volume,
      cancelRequestIdFactory: () => 'cancel-controller-v4',
      onCanvasClosed: canvasHost.detach,
      onMicrophoneTrackChanged: onMicrophoneTrackChanged,
      automaticPlaybackPolling: false,
      microphoneInitiallyEnabled: false,
    );
    runtime = created;
    canvasHost.attach(created.canvasController);
    return created;
  }

  @override
  Future<void> close() async {
    await runtime?.close();
  }
}

final class _PushRawTransportV4 implements AvaRealtimeRawTransportV4 {
  final StreamController<AvaRealtimeInboundFrameV4> _frames =
      StreamController<AvaRealtimeInboundFrameV4>();
  final List<AvaRealtimeClientEventV4> sentEvents = [];
  final Map<String, List<Completer<void>>> _waiters = {};
  bool closed = false;

  List<Map<String, Object?>> get sentJson => [
        for (final event in sentEvents) event.toJson(),
      ];

  void add(AvaRealtimeInboundFrameV4 frame) => _frames.add(frame);

  Future<void> waitForSent(String type) {
    if (sentJson.any((event) => event['event'] == type)) return Future.value();
    final result = Completer<void>();
    _waiters.putIfAbsent(type, () => []).add(result);
    return result.future;
  }

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {
    sentEvents.add(event);
    final type = event.toJson()['event'];
    if (type is! String) return;
    for (final waiter in _waiters.remove(type) ?? const []) {
      waiter.complete();
    }
  }

  @override
  Future<void> close() async {
    if (closed) return;
    closed = true;
    await _frames.close();
  }
}

final class _UnexpectedDocumentLoader implements AvaSvgDocumentLoader {
  const _UnexpectedDocumentLoader();

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) =>
      throw StateError('Unexpected canvas document fetch.');

  @override
  void close() {}
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
