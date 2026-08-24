import 'dart:typed_data';

import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_session_v4.dart';
import 'package:ava/services/ava_realtime_turn_runtime.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _sessionId = 'session-runtime-v4';
const _turnId = 'turn-runtime-v4';
const _streamId = 'stream-runtime-v4';
const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava runtime v4 test\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('turnCreated cannot replace an active V4 turn', () async {
    final runtime = _runtime([
      ..._preamble().take(2),
      _control({
        'protocolVersion': 4,
        'event': 'turnCreated',
        'sessionId': _sessionId,
        'turnId': 'turn-replacement',
        'sequence': 2,
        'revision': 0,
        'requestId': 'request-replacement',
      }),
    ]);

    await expectLater(
      runtime.run(),
      throwsA(isA<AvaRealtimeRuntimeException>()),
    );
    await runtime.close();
  });

  test('PCM descriptor requires immediate binary adjacency', () async {
    final frames = <AvaRealtimeInboundFrameV4>[
      ..._preamble(),
      _control(_audioChunk(sequence: 7, byteLength: 8)),
      _control({
        'protocolVersion': 4,
        'event': 'alignmentBatch',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 8,
        'revision': 4,
        'streamId': _streamId,
        'spans': [
          {
            'segmentId': 'reply',
            'textStart': 0,
            'textEnd': 1,
            'sampleStart': 0,
            'sampleEnd': 4,
            'phoneme': 'AH',
          },
        ],
      }),
    ];
    final runtime = _runtime(frames);

    await expectLater(
      runtime.run(),
      throwsA(
        isA<AvaRealtimeRuntimeException>().having(
          (error) => error.message,
          'message',
          contains('followed immediately'),
        ),
      ),
    );
    await runtime.close();
  });

  test('binary PCM must exactly match its V4 descriptor', () async {
    final runtime = _runtime([
      ..._preamble(),
      _control(_audioChunk(sequence: 7, byteLength: 8)),
      AvaRealtimePcmFrameV4(Uint8List(6)),
    ]);

    await expectLater(
      runtime.run(),
      throwsA(
        isA<AvaRealtimeRuntimeException>().having(
          (error) => error.message,
          'message',
          contains('does not match descriptor'),
        ),
      ),
    );
    await runtime.close();
  });

  test('caption presentation remains bounded and sample-clock driven', () {
    final presentation = AvaRealtimePresentationController();
    presentation.beginTurn(sessionId: _sessionId, turnId: _turnId);
    presentation.setPhase(AvaRealtimeTurnPhase.speaking);
    presentation.addSegment(
      0,
      AvaRealtimeTurnSegment.fromJson({
        'id': 'reply',
        'speech': 'First sentence. Second sentence.',
        'delivery': 'neutral',
        'expression': 'neutral',
        'gaze': 'child',
        'motionIntent': 'none',
        'canvasCues': <Object>[],
      }),
    );
    presentation.addAlignmentSpans([
      AvaRealtimeAlignmentSpan.fromJson({
        'segmentId': 'reply',
        'textStart': 16,
        'textEnd': 22,
        'sampleStart': 2400,
        'sampleEnd': 4800,
        'phoneme': 'EE',
      }),
    ]);

    presentation.updatePlaybackClock(
      renderedSampleCount: 3000,
      receivedSampleCount: 4800,
    );

    expect(presentation.state.caption, 'Second sentence.');
    expect(
      presentation.state.lipSync!.cues.single.viseme,
      AvaRealtimeViseme.ee,
    );
    expect(presentation.state.issues, isEmpty);
  });
}

AvaRealtimeTurnRuntime _runtime(List<AvaRealtimeInboundFrameV4> frames) =>
    AvaRealtimeTurnRuntime(
      sessionId: _sessionId,
      limits: const AvaSessionLimitsV4(
        maxTurnSeconds: 45,
        maxResponseCharacters: 1200,
        maxSvgBytes: 262144,
        maxManifestBytes: 65536,
        maxAudioChunkBytes: 192000,
        playbackProgressIntervalMs: 100,
      ),
      mediaSession: DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp),
      transport: DeterministicAvaRealtimeRawTransportV4(frames),
      documents: const _UnexpectedDocumentLoader(),
      speechOutput: _RecordingPcmOutput(),
      presentation: AvaRealtimePresentationController(),
      volume: 0.8,
      cancelRequestIdFactory: () => 'cancel-runtime-v4',
      automaticPlaybackPolling: false,
      microphoneInitiallyEnabled: false,
    );

List<AvaRealtimeControlFrameV4> _preamble() => [
      _control({
        'protocolVersion': 4,
        'event': 'turnCreated',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 0,
        'revision': 0,
        'requestId': 'request-runtime-v4',
      }),
      for (final phase in const [
        (1, 1, 'connecting'),
        (2, 2, 'listening'),
        (3, 3, 'planning'),
      ])
        _control({
          'protocolVersion': 4,
          'event': 'turnPhaseChanged',
          'sessionId': _sessionId,
          'turnId': _turnId,
          'sequence': phase.$1,
          'revision': phase.$2,
          'phase': phase.$3,
        }),
      _control({
        'protocolVersion': 4,
        'event': 'segmentReady',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 4,
        'revision': 3,
        'segmentIndex': 0,
        'segment': {
          'id': 'reply',
          'speech': 'A plant cell is a tiny living system.',
          'delivery': 'warm',
          'expression': 'curious',
          'gaze': 'child',
          'motionIntent': 'acknowledge',
          'canvasCues': <Object>[],
        },
      }),
      _control({
        'protocolVersion': 4,
        'event': 'turnPhaseChanged',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 5,
        'revision': 4,
        'phase': 'synthesizing',
      }),
      _control({
        'protocolVersion': 4,
        'event': 'audioMetadata',
        'sessionId': _sessionId,
        'turnId': _turnId,
        'sequence': 6,
        'revision': 4,
        'streamId': _streamId,
        'audioFormat': 'pcm_s16le',
        'sampleRateHz': 24000,
        'channels': 1,
        'bitsPerSample': 16,
        'modelId': 'elevenlabs-test-model',
        'voiceId': 'ava-test-voice',
      }),
    ];

Map<String, Object?> _audioChunk({
  required int sequence,
  required int byteLength,
}) =>
    {
      'protocolVersion': 4,
      'event': 'audioChunk',
      'sessionId': _sessionId,
      'turnId': _turnId,
      'sequence': sequence,
      'revision': 4,
      'streamId': _streamId,
      'chunkIndex': 0,
      'sampleOffset': 0,
      'sampleCount': 4,
      'byteLength': byteLength,
    };

AvaRealtimeControlFrameV4 _control(Map<String, Object?> json) =>
    AvaRealtimeControlFrameV4(AvaRealtimeServerEventV4.fromJson(json));

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

final class _RecordingPcmOutput implements PcmSpeechOutputService {
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
