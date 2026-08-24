import 'dart:async';
import 'dart:convert';
import 'dart:io';
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

const _sessionId = 'session-v4-characterization';
const _deterministicOfferSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava V4 characterization offer\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

void main() {
  test('normal direct V4 turn preserves wire order, PCM, and microphone intent',
      () async {
    final scenario = _DirectV4Scenario(sessionId: _sessionId);
    await scenario.start();
    await scenario.runtime.setMicrophoneEnabled(true);

    await scenario.completeTurn(
      turnId: 'turn-v4-normal',
      streamId: 'stream-v4-normal',
    );

    expect(scenario.presentation.state.phase, AvaRealtimeTurnPhase.completed);
    expect(scenario.presentation.state.turnId, 'turn-v4-normal');
    expect(scenario.presentation.state.deliveredText, isNotEmpty);
    expect(scenario.media.microphoneEnabled, isTrue);
    expect(scenario.pcm.startedTurnIds, ['turn-v4-normal']);
    expect(scenario.pcm.appendedSamples['turn-v4-normal'], 8);
    final controls = scenario.raw.sentJson;
    expect(
      controls.map((event) => event['event']),
      ['clientReady', 'playbackStarted', 'playbackProgress'],
    );
    expect(controls.map((event) => event['sequence']), [0, 1, 2]);
    expect(controls[1]['turnId'], 'turn-v4-normal');
    expect(controls[1]['streamId'], 'stream-v4-normal');
    expect(controls[1]['renderedSampleCount'], 0);
    expect(controls[2]['renderedSampleCount'], 8);
    expect(controls[2]['bufferedSampleCount'], 0);

    await scenario.close();
  });

  test('three direct V4 turns retain one session and reset each sample clock',
      () async {
    final scenario = _DirectV4Scenario(sessionId: _sessionId);
    await scenario.start();
    await scenario.runtime.setMicrophoneEnabled(true);
    const turns = [
      ('turn-v4-one', 'stream-v4-one'),
      ('turn-v4-two', 'stream-v4-two'),
      ('turn-v4-three', 'stream-v4-three'),
    ];

    for (final (turnId, streamId) in turns) {
      await scenario.completeTurn(turnId: turnId, streamId: streamId);
      expect(scenario.media.microphoneEnabled, isTrue);
    }

    expect(
      scenario.pcm.startedTurnIds,
      turns.map((turn) => turn.$1).toList(),
    );
    expect(
      scenario.pcm.samplesAtStart,
      {for (final turn in turns) turn.$1: 0},
    );
    final controls = scenario.raw.sentJson;
    expect(
      controls.where((event) => event['event'] == 'clientReady'),
      hasLength(1),
    );
    final starts =
        controls.where((event) => event['event'] == 'playbackStarted').toList();
    expect(starts.map((event) => event['turnId']), [
      'turn-v4-one',
      'turn-v4-two',
      'turn-v4-three',
    ]);
    expect(starts.map((event) => event['renderedSampleCount']), [0, 0, 0]);
    expect(
      controls.map((event) => event['sequence']),
      List<int>.generate(controls.length, (index) => index),
    );
    expect(scenario.presentation.state.turnId, 'turn-v4-three');
    expect(scenario.presentation.state.renderedSampleCount, 8);

    await scenario.close();
  });

  test('VAD interruption fades PCM and reports the exact native final sample',
      () async {
    final scenario = _DirectV4Scenario(
      sessionId: _sessionId,
      fadeStopSample: 3,
    );
    await scenario.start();
    await scenario.runtime.setMicrophoneEnabled(true);
    await scenario.sendTurnUntilSpeaking(
      turnId: 'turn-v4-vad',
      streamId: 'stream-v4-vad',
    );
    scenario.addControl({
      'protocolVersion': 4,
      'event': 'canvasGenerationStarted',
      'sessionId': _sessionId,
      'revision': 1,
      'generationId': 'generation-v4-vad',
      'sourceTurnId': 'turn-v4-vad',
    });

    scenario.addControl({
      'protocolVersion': 4,
      'event': 'interruptRequested',
      'sessionId': _sessionId,
      'turnId': 'turn-v4-vad',
      'revision': 5,
      'streamId': 'stream-v4-vad',
      'reason': 'providerSpeechStarted',
    });
    final stopped = await scenario.raw.waitForSent(
      (event) => event['event'] == 'playbackStopped',
    );
    final cancelled = await scenario.raw.waitForSent(
      (event) => event['event'] == 'cancelTurn',
    );

    expect(scenario.pcm.fadeStoppedTurnIds, ['turn-v4-vad']);
    expect(stopped['turnId'], 'turn-v4-vad');
    expect(stopped['streamId'], 'stream-v4-vad');
    expect(stopped['renderedSampleCount'], 3);
    expect(cancelled['turnId'], 'turn-v4-vad');
    expect(cancelled['reason'], 'providerSpeechStarted');
    expect(scenario.media.microphoneEnabled, isTrue);
    final canvasCancelled = Completer<void>();
    void observeCanvasCancellation() {
      if (scenario.runtime.canvasController.generation == null &&
          !canvasCancelled.isCompleted) {
        canvasCancelled.complete();
      }
    }

    scenario.runtime.canvasController.addListener(observeCanvasCancellation);
    scenario.addControl({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': _sessionId,
      'revision': 1,
      'generationId': 'generation-v4-vad',
      'sourceTurnId': 'turn-v4-vad',
      'rejectionCode': 'cancelled',
      'message': 'Canvas generation was cancelled by interruption.',
      'retryable': false,
    });
    await canvasCancelled.future;
    scenario.runtime.canvasController.removeListener(
      observeCanvasCancellation,
    );
    expect(scenario.runtime.canvasController.generation, isNull);
    expect(scenario.runtime.canvasController.lastIssue, isNull);

    scenario.addControl({
      'protocolVersion': 4,
      'event': 'turnPhaseChanged',
      'sessionId': _sessionId,
      'turnId': 'turn-v4-vad',
      'revision': 6,
      'phase': 'cancelling',
    });
    scenario.addControl({
      'protocolVersion': 4,
      'event': 'turnCancelled',
      'sessionId': _sessionId,
      'turnId': 'turn-v4-vad',
      'revision': 7,
      'deliveredSampleCount': 3,
      'deliveredText': 'A',
      'reason': 'providerSpeechStarted',
    });
    await scenario.waitForPresentation(
      (state) => state.phase == AvaRealtimeTurnPhase.cancelled,
    );
    expect(scenario.presentation.state.renderedSampleCount, 3);
    expect(scenario.presentation.state.deliveredText, 'A');

    await scenario.close();
  });

  test('canvas and memory failures remain isolated from direct V4 speech',
      () async {
    final scenario = _DirectV4Scenario(sessionId: _sessionId);
    await scenario.start();
    await scenario.runtime.setMicrophoneEnabled(true);
    await scenario.sendTurnUntilSpeaking(
      turnId: 'turn-v4-optional-failure',
      streamId: 'stream-v4-optional-failure',
    );

    scenario.addControl({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': _sessionId,
      'revision': 1,
      'generationId': 'generation-v4-failed',
      'sourceTurnId': 'turn-v4-optional-failure',
      'rejectionCode': 'providerUnavailable',
      'message': 'The optional visual provider was unavailable.',
      'retryable': true,
    });
    scenario.addControl({
      'protocolVersion': 4,
      'event': 'memoryPersistenceFailed',
      'sessionId': _sessionId,
      'turnId': 'turn-v4-optional-failure',
      'revision': 5,
      'failure': {
        'failureId': 'failure-v4-memory',
        'component': 'memoryStore',
        'code': 'memoryUnavailable',
        'message': 'Memory persistence was unavailable.',
        'retryable': true,
      },
    });

    await scenario.finishCurrentTurn(
      turnId: 'turn-v4-optional-failure',
      streamId: 'stream-v4-optional-failure',
    );

    expect(scenario.presentation.state.phase, AvaRealtimeTurnPhase.completed);
    expect(scenario.presentation.state.memoryWarning, isNotNull);
    expect(
      scenario.runtime.canvasController.lastIssue?.message,
      'The optional visual provider was unavailable.',
    );
    expect(scenario.media.microphoneEnabled, isTrue);
    expect(scenario.pcm.stopCalled, isFalse);
    expect(
      scenario.raw.sentJson.map((event) => event['event']),
      ['clientReady', 'playbackStarted', 'playbackProgress'],
    );

    await scenario.close();
  });

  test('close and reconnect create a fresh V4 control and presentation scope',
      () async {
    final first = _DirectV4Scenario(sessionId: 'session-v4-before-reconnect');
    await first.start();
    first.addControl({
      'protocolVersion': 4,
      'event': 'turnCreated',
      'sessionId': 'session-v4-before-reconnect',
      'turnId': 'turn-v4-before-reconnect',
      'requestId': 'request-v4-before-reconnect',
      'revision': 0,
    });
    await first.waitForPresentation(
      (state) => state.turnId == 'turn-v4-before-reconnect',
    );
    await first.close();
    expect(first.raw.closed, isTrue);
    expect(first.media.closed, isTrue);

    final second = _DirectV4Scenario(sessionId: 'session-v4-after-reconnect');
    await second.start();
    await second.runtime.setMicrophoneEnabled(true);
    await second.completeTurn(
      turnId: 'turn-v4-after-reconnect',
      streamId: 'stream-v4-after-reconnect',
    );

    expect(second.raw.sentJson.first['sequence'], 0);
    expect(
        second.raw.sentJson.first['sessionId'], 'session-v4-after-reconnect');
    expect(second.presentation.state.turnId, 'turn-v4-after-reconnect');
    expect(second.presentation.state.phase, AvaRealtimeTurnPhase.completed);
    expect(second.media.microphoneEnabled, isTrue);

    await second.close();
  });
}

final class _DirectV4Scenario {
  _DirectV4Scenario({
    required this.sessionId,
    int fadeStopSample = 0,
  })  : raw = _ControlledRawTransportV4(),
        pcm = _ControlledPcmOutputV4(fadeStopSample: fadeStopSample),
        media = DeterministicAvaRealtimeMediaSession(
          offerSdp: _deterministicOfferSdp,
        ),
        presentation = AvaRealtimePresentationController() {
    runtime = AvaRealtimeTurnRuntime(
      sessionId: sessionId,
      limits: AvaSessionBootstrapV4.fromJson(
        _objectFixture('ava_session_bootstrap_v4.json'),
      ).limits,
      mediaSession: media,
      transport: raw,
      documents: _UnexpectedDocumentLoader(),
      speechOutput: pcm,
      presentation: presentation,
      volume: 0.8,
      cancelRequestIdFactory: () => 'cancel-v4-characterization',
      automaticPlaybackPolling: false,
      microphoneInitiallyEnabled: false,
    );
  }

  final String sessionId;
  final _ControlledRawTransportV4 raw;
  final _ControlledPcmOutputV4 pcm;
  final DeterministicAvaRealtimeMediaSession media;
  final AvaRealtimePresentationController presentation;
  late final AvaRealtimeTurnRuntime runtime;
  late final Future<void> _run;
  int _nextServerSequence = 0;
  bool _started = false;
  bool _closed = false;

  Future<void> start() async {
    if (_started) throw StateError('Direct V4 scenario already started.');
    _started = true;
    _run = runtime.run();
    await raw.waitForSent((event) => event['event'] == 'clientReady');
  }

  void addControl(Map<String, Object?> payload) {
    if (payload['sessionId'] != sessionId) {
      throw StateError('Scenario control changed session identity.');
    }
    final event = AvaRealtimeServerEventV4.fromJson({
      ...payload,
      'sequence': _nextServerSequence,
    });
    _nextServerSequence += 1;
    raw.add(AvaRealtimeControlFrameV4(event));
  }

  void addPcm(int byteLength) {
    raw.add(AvaRealtimePcmFrameV4(Uint8List(byteLength)));
  }

  Future<void> sendTurnUntilSpeaking({
    required String turnId,
    required String streamId,
  }) async {
    final events = _speechFixtureForTurn(
      sessionId: sessionId,
      turnId: turnId,
      streamId: streamId,
    );
    for (final payload in events.take(events.length - 1)) {
      addControl(payload);
      if (payload['event'] == 'audioChunk') {
        addPcm(payload['byteLength']! as int);
      }
    }
    await pcm.waitForFinish(turnId);
  }

  Future<void> finishCurrentTurn({
    required String turnId,
    required String streamId,
  }) async {
    pcm.completePlayback(turnId);
    await raw.waitForSent(
      (event) =>
          event['event'] == 'playbackProgress' &&
          event['turnId'] == turnId &&
          event['renderedSampleCount'] == 8,
    );
    final terminal = _speechFixtureForTurn(
      sessionId: sessionId,
      turnId: turnId,
      streamId: streamId,
    ).last;
    addControl(terminal);
    await waitForPresentation(
      (state) =>
          state.turnId == turnId &&
          state.phase == AvaRealtimeTurnPhase.completed,
    );
  }

  Future<void> completeTurn({
    required String turnId,
    required String streamId,
  }) async {
    await sendTurnUntilSpeaking(turnId: turnId, streamId: streamId);
    await finishCurrentTurn(turnId: turnId, streamId: streamId);
  }

  Future<void> waitForPresentation(
    bool Function(AvaRealtimePresentationState state) predicate,
  ) {
    if (predicate(presentation.state)) return Future<void>.value();
    final result = Completer<void>();
    late final void Function() listener;
    listener = () {
      if (!predicate(presentation.state) || result.isCompleted) return;
      presentation.removeListener(listener);
      result.complete();
    };
    presentation.addListener(listener);
    return result.future;
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await runtime.close();
    await _run;
  }
}

final class _ControlledRawTransportV4 implements AvaRealtimeRawTransportV4 {
  final StreamController<AvaRealtimeInboundFrameV4> _frames =
      StreamController<AvaRealtimeInboundFrameV4>();
  final List<AvaRealtimeClientEventV4> sentEvents = [];
  final List<_SentWaiter> _waiters = [];
  bool closed = false;

  List<Map<String, Object?>> get sentJson => [
        for (final event in sentEvents) event.toJson(),
      ];

  void add(AvaRealtimeInboundFrameV4 frame) {
    if (closed) throw StateError('Controlled V4 transport is closed.');
    _frames.add(frame);
  }

  Future<Map<String, Object?>> waitForSent(
    bool Function(Map<String, Object?> event) predicate,
  ) {
    for (final event in sentJson) {
      if (predicate(event)) return Future<Map<String, Object?>>.value(event);
    }
    final result = Completer<Map<String, Object?>>();
    _waiters.add(_SentWaiter(predicate: predicate, result: result));
    return result.future;
  }

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {
    if (closed) throw StateError('Controlled V4 transport is closed.');
    sentEvents.add(event);
    final json = event.toJson();
    for (final waiter in List<_SentWaiter>.of(_waiters)) {
      if (!waiter.predicate(json)) continue;
      _waiters.remove(waiter);
      waiter.result.complete(json);
    }
  }

  @override
  Future<void> close() async {
    if (closed) return;
    closed = true;
    await _frames.close();
    for (final waiter in _waiters) {
      if (!waiter.result.isCompleted) {
        waiter.result.completeError(
          StateError('V4 transport closed before the expected client control.'),
        );
      }
    }
    _waiters.clear();
  }
}

final class _SentWaiter {
  const _SentWaiter({
    required this.predicate,
    required this.result,
  });

  final bool Function(Map<String, Object?> event) predicate;
  final Completer<Map<String, Object?>> result;
}

final class _ControlledPcmOutputV4 implements PcmSpeechOutputService {
  _ControlledPcmOutputV4({required this.fadeStopSample});

  final int fadeStopSample;
  final List<String> startedTurnIds = [];
  final List<String> fadeStoppedTurnIds = [];
  final Map<String, int> samplesAtStart = {};
  final Map<String, int> appendedSamples = {};
  final Map<String, Completer<void>> _finishRequested = {};
  final Map<String, Completer<PcmStopResult>> _finishResults = {};
  bool stopCalled = false;

  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {
    if (sampleRateHz != 24000) throw StateError('Unexpected sample rate.');
    startedTurnIds.add(turnId);
    samplesAtStart[turnId] = 0;
    appendedSamples[turnId] = 0;
  }

  @override
  Future<void> append({
    required String turnId,
    required Uint8List audio,
  }) async {
    final previous = appendedSamples[turnId];
    if (previous == null) throw StateError('PCM append occurred before start.');
    appendedSamples[turnId] = previous + audio.length ~/ 2;
  }

  @override
  Future<PcmStopResult> finish({required String turnId}) {
    _finishRequested.putIfAbsent(turnId, Completer<void>.new).complete();
    return _finishResults
        .putIfAbsent(
          turnId,
          Completer<PcmStopResult>.new,
        )
        .future;
  }

  Future<void> waitForFinish(String turnId) {
    return _finishRequested.putIfAbsent(turnId, Completer<void>.new).future;
  }

  void completePlayback(String turnId) {
    final sampleCount = appendedSamples[turnId];
    if (sampleCount == null) throw StateError('Unknown PCM turn $turnId.');
    final result = _finishResults.putIfAbsent(
      turnId,
      Completer<PcmStopResult>.new,
    );
    if (!result.isCompleted) {
      result.complete(PcmStopResult(renderedSampleCount: sampleCount));
    }
  }

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async {
    final samples = appendedSamples[turnId];
    if (samples == null) throw StateError('Unknown PCM turn $turnId.');
    return PcmPlaybackProgress(
      renderedSampleCount: 0,
      scheduledSampleCount: samples,
      sampleRateHz: 24000,
    );
  }

  @override
  Future<Duration> position({required String turnId}) async => Duration.zero;

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async {
    fadeStoppedTurnIds.add(turnId);
    final finish = _finishResults[turnId];
    if (finish != null && !finish.isCompleted) {
      finish.completeError(StateError('PCM playback interrupted.'));
    }
    return PcmStopResult(renderedSampleCount: fadeStopSample);
  }

  @override
  Future<void> stop() async {
    stopCalled = true;
  }
}

final class _UnexpectedDocumentLoader implements AvaSvgDocumentLoader {
  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) {
    throw StateError(
      'No canvas document should be fetched in this speech characterization.',
    );
  }

  @override
  void close() {}
}

List<Map<String, Object?>> _speechFixtureForTurn({
  required String sessionId,
  required String turnId,
  required String streamId,
}) =>
    [
      {
        'protocolVersion': 4,
        'event': 'turnCreated',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 0,
        'requestId': 'request-$turnId',
      },
      {
        'protocolVersion': 4,
        'event': 'turnPhaseChanged',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 1,
        'phase': 'connecting',
      },
      {
        'protocolVersion': 4,
        'event': 'turnPhaseChanged',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 2,
        'phase': 'listening',
      },
      {
        'protocolVersion': 4,
        'event': 'turnPhaseChanged',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 3,
        'phase': 'planning',
      },
      {
        'protocolVersion': 4,
        'event': 'segmentReady',
        'sessionId': sessionId,
        'turnId': turnId,
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
      },
      {
        'protocolVersion': 4,
        'event': 'turnPhaseChanged',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 4,
        'phase': 'synthesizing',
      },
      {
        'protocolVersion': 4,
        'event': 'audioMetadata',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 4,
        'streamId': streamId,
        'audioFormat': 'pcm_s16le',
        'sampleRateHz': 24000,
        'channels': 1,
        'bitsPerSample': 16,
        'modelId': 'elevenlabs-test-model',
        'voiceId': 'ava-test-voice',
      },
      {
        'protocolVersion': 4,
        'event': 'alignmentBatch',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 4,
        'streamId': streamId,
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
      },
      {
        'protocolVersion': 4,
        'event': 'audioChunk',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 4,
        'streamId': streamId,
        'chunkIndex': 0,
        'sampleOffset': 0,
        'sampleCount': 4,
        'byteLength': 8,
      },
      {
        'protocolVersion': 4,
        'event': 'audioChunk',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 4,
        'streamId': streamId,
        'chunkIndex': 1,
        'sampleOffset': 4,
        'sampleCount': 4,
        'byteLength': 8,
      },
      {
        'protocolVersion': 4,
        'event': 'turnPhaseChanged',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 5,
        'phase': 'speaking',
      },
      {
        'protocolVersion': 4,
        'event': 'turnCompleted',
        'sessionId': sessionId,
        'turnId': turnId,
        'revision': 6,
        'deliveredSampleCount': 8,
        'deliveredText': 'A plant cell is a tiny living system.',
      },
    ];

Map<String, Object?> _objectFixture(String name) {
  final decoded = jsonDecode(
    File('protocol/fixtures/$name').readAsStringSync(),
  );
  return (decoded as Map).cast<String, Object?>();
}
