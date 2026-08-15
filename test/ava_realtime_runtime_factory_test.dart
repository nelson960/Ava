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
import 'package:ava/services/ava_realtime_trace.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava persistent offer\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('create negotiates one complete persistent runtime while muted',
      () async {
    final bootstrapTransport = _BootstrapTransportFake();
    final mediaSessions = <DeterministicAvaRealtimeMediaSession>[];
    final transports = <DeterministicAvaRealtimeTransport>[];
    final traceEvents = <AvaRealtimeTraceEvent>[];
    final factory = AvaRealtimeRuntimeFactory(
      bootstrapService: AvaRealtimeBootstrapService(
        configuration: AvaBackendConfiguration.parse(
          'https://ava.example.test',
        ),
        transport: bootstrapTransport,
      ),
      mediaConnector: (bootstrap) async {
        final media = DeterministicAvaRealtimeMediaSession(
          offerSdp: _offerSdp,
        );
        mediaSessions.add(media);
        return media;
      },
      transportConnector: (bootstrap) async {
        final transport = DeterministicAvaRealtimeTransport(const []);
        transports.add(transport);
        return transport;
      },
      speechOutput: const _NoopPcmOutput(),
      canvasEngine: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
      cancelRequestIdFactory: () => 'cancel-runtime-factory-test',
      traceFactory: () => AvaRealtimeTrace(
        attemptId: 'attempt-runtime-factory',
        observer: traceEvents.add,
      ),
    );
    addTearDown(factory.close);

    final runtime = await factory.create(
      ageBand: AvaRealtimeAgeBand.middleChildhood,
      volume: 0.8,
    );

    expect(bootstrapTransport.requestCount, 1);
    expect(mediaSessions, hasLength(1));
    expect(transports, hasLength(1));
    expect(mediaSessions.single.microphoneEnabled, isFalse);
    expect(
      traceEvents.map((event) => event.stage),
      containsAllInOrder(const [
        'connection.requested',
        'bootstrap.create',
        'webrtc.connect',
        'events.connect',
        'runtime.ready',
      ]),
    );

    await runtime.close();
    expect(mediaSessions.single.closed, isTrue);
    expect(transports.single.isClosed, isTrue);
  });

  test('a closed factory cannot silently create a replacement session',
      () async {
    final factory = AvaRealtimeRuntimeFactory(
      bootstrapService: AvaRealtimeBootstrapService(
        configuration: AvaBackendConfiguration.parse(
          'https://ava.example.test',
        ),
        transport: _BootstrapTransportFake(),
      ),
      mediaConnector: (bootstrap) async =>
          DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp),
      transportConnector: (bootstrap) async =>
          DeterministicAvaRealtimeTransport(const []),
      speechOutput: const _NoopPcmOutput(),
      canvasEngine: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
      cancelRequestIdFactory: () => 'cancel-runtime-factory-test',
    );

    await factory.close();

    await expectLater(
      factory.create(
        ageBand: AvaRealtimeAgeBand.middleChildhood,
        volume: 1,
      ),
      throwsA(isA<StateError>()),
    );
  });
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
    final id = 'session-$requestCount-prepared';
    final payload = <String, Object?>{
      'protocolVersion': 3,
      'sessionId': id,
      'eventUrl': 'wss://ava.example.test/v3/realtime/$id/events',
      'webrtcUrl': 'https://ava.example.test/v3/realtime/$id/webrtc',
      'sessionToken': 'session-token-$requestCount',
      'webrtcToken': 'webrtc-token-$requestCount',
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
