import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:ava/models/ava_svg_canvas_protocol.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_session_v4.dart';
import 'package:ava/services/ava_realtime_turn_runtime.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/ava_svg_document_service.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

const _sessionId = 'session-1234';
const _offerSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava direct v4 runtime test\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';
const _manifestJson =
    '{"background":"#dff5ff","bindings":[{"accessibilityLabel":"Rain cloud","bindingId":"select-cloud","elementId":"cloud","revealIds":[],"targetId":"cloud"}],"description":"A cloud turns water vapour into rain.","interactionMode":"select","manifestVersion":"ava-svg-manifest-v2","rendererCapabilityVersion":"ava-svg-renderer-v2","sourceTrust":"modelGeneratedData","title":"Café cloud ☁️","validatorVersion":"ava-svg-validator-v2","viewBox":[0.0,0.0,1000.0,600.0]}';

void main() {
  test('runtime rejects a gap in the one global v4 server sequence', () async {
    final harness = _RuntimeHarness();
    await harness.start();

    harness.addControl(_turnCreated(sequence: 1));

    await expectLater(
      harness.runFuture,
      throwsA(isA<AvaRealtimeStateException>()),
    );
    await harness.close();
  });

  test('speech and SVG controls share one outbound v4 sequence', () async {
    final harness = _RuntimeHarness(document: _validatedDocument());
    await harness.start();
    harness.addControl(_turnCreated(sequence: 0));
    await harness.waitForPresentation(
      (state) => state.turnId == 'turn-1234',
    );

    final started = _objectFixture('ava_realtime_canvas_generation_v4.json');
    harness.addControl({...started, 'sequence': 1});
    final available = _listFixture('ava_realtime_canvas_events_v4.json').first;
    harness.addControl({...available, 'sequence': 2, 'revision': 2});
    await harness.waitForCanvas(
      () =>
          harness.runtime.canvasController.candidate?.phase.name ==
          'rendererPreparing',
    );
    harness.runtime.canvasController.rendererPrepared();
    await harness.raw.waitForSent(
      (event) => event['event'] == 'canvasDocumentPrepared',
    );

    harness.addControl({
      'protocolVersion': 4,
      'event': 'turnPhaseChanged',
      'sessionId': _sessionId,
      'turnId': 'turn-1234',
      'sequence': 3,
      'revision': 1,
      'phase': 'connecting',
    });
    await harness.waitForPresentation(
      (state) => state.phase == AvaRealtimeTurnPhase.connecting,
    );

    expect(harness.raw.sentJson.map((event) => event['sequence']), [0, 1]);
    expect(
      harness.raw.sentJson.map((event) => event['event']),
      ['clientReady', 'canvasDocumentPrepared'],
    );
    await harness.close();
  });

  test('closing while raw receive is suspended leaves no live runtime owner',
      () async {
    final harness = _RuntimeHarness();
    await harness.start();

    await harness.close();

    expect(harness.raw.closed, isTrue);
    expect(harness.media.closed, isTrue);
    expect(harness.loader.closed, isTrue);
    await expectLater(harness.runFuture, completes);
  });

  test('turn identity resets the SVG rendered-sample clock', () async {
    final harness = _RuntimeHarness();
    await harness.start();
    harness.addControl(_turnCreated(sequence: 0, turnId: 'turn-one'));
    await harness.waitForPresentation((state) => state.turnId == 'turn-one');
    expect(harness.runtime.canvasController.renderedSampleTurnId, 'turn-one');
    await harness.close();
  });
}

final class _RuntimeHarness {
  _RuntimeHarness({AvaValidatedSvgCanvasDocument? document})
      : raw = _PushRawTransportV4(),
        media = DeterministicAvaRealtimeMediaSession(offerSdp: _offerSdp),
        presentation = AvaRealtimePresentationController(),
        loader = _Loader(document) {
    runtime = AvaRealtimeTurnRuntime(
      sessionId: _sessionId,
      limits: AvaSessionBootstrapV4.fromJson(
        _objectFixture('ava_session_bootstrap_v4.json'),
      ).limits,
      mediaSession: media,
      transport: raw,
      documents: loader,
      speechOutput: const _NoopPcmOutput(),
      presentation: presentation,
      volume: 0.8,
      cancelRequestIdFactory: () => 'cancel-direct-v4-test',
      automaticPlaybackPolling: false,
      microphoneInitiallyEnabled: false,
    );
  }

  final _PushRawTransportV4 raw;
  final DeterministicAvaRealtimeMediaSession media;
  final AvaRealtimePresentationController presentation;
  final _Loader loader;
  late final AvaRealtimeTurnRuntime runtime;
  late final Future<void> runFuture;
  bool _closed = false;

  Future<void> start() async {
    runFuture = runtime.run();
    await raw.waitForSent((event) => event['event'] == 'clientReady');
  }

  void addControl(Map<String, Object?> json) {
    raw.add(
      AvaRealtimeControlFrameV4(AvaRealtimeServerEventV4.fromJson(json)),
    );
  }

  Future<void> waitForPresentation(
    bool Function(AvaRealtimePresentationState state) predicate,
  ) {
    if (predicate(presentation.state)) return Future<void>.value();
    final result = Completer<void>();
    late final VoidCallback listener;
    listener = () {
      if (!predicate(presentation.state) || result.isCompleted) return;
      presentation.removeListener(listener);
      result.complete();
    };
    presentation.addListener(listener);
    return result.future;
  }

  Future<void> waitForCanvas(bool Function() predicate) {
    if (predicate()) return Future<void>.value();
    final result = Completer<void>();
    late final VoidCallback listener;
    listener = () {
      if (!predicate() || result.isCompleted) return;
      runtime.canvasController.removeListener(listener);
      result.complete();
    };
    runtime.canvasController.addListener(listener);
    return result.future;
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await runtime.close();
  }
}

final class _PushRawTransportV4 implements AvaRealtimeRawTransportV4 {
  final StreamController<AvaRealtimeInboundFrameV4> _frames =
      StreamController<AvaRealtimeInboundFrameV4>();
  final List<AvaRealtimeClientEventV4> sentEvents = [];
  final List<
      ({
        bool Function(Map<String, Object?>) predicate,
        Completer<Map<String, Object?>> result,
      })> _waiters = [];
  bool closed = false;

  List<Map<String, Object?>> get sentJson => [
        for (final event in sentEvents) event.toJson(),
      ];

  void add(AvaRealtimeInboundFrameV4 frame) => _frames.add(frame);

  Future<Map<String, Object?>> waitForSent(
    bool Function(Map<String, Object?> event) predicate,
  ) {
    for (final event in sentJson) {
      if (predicate(event)) return Future.value(event);
    }
    final result = Completer<Map<String, Object?>>();
    _waiters.add((predicate: predicate, result: result));
    return result.future;
  }

  @override
  Stream<AvaRealtimeInboundFrameV4> frames() => _frames.stream;

  @override
  Future<void> send(AvaRealtimeClientEventV4 event) async {
    sentEvents.add(event);
    final json = event.toJson();
    for (final waiter in List.of(_waiters)) {
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
  }
}

final class _Loader implements AvaSvgDocumentLoader {
  _Loader(this.document);

  final AvaValidatedSvgCanvasDocument? document;
  bool closed = false;

  @override
  Future<AvaValidatedSvgCanvasDocument> fetch(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
  ) async {
    final value = document;
    if (value == null) throw StateError('Unexpected SVG document fetch.');
    return value;
  }

  @override
  void close() => closed = true;
}

final class _NoopPcmOutput implements PcmSpeechOutputService {
  const _NoopPcmOutput();

  @override
  Future<void> append({required String turnId, required Uint8List audio}) =>
      Future.value();

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
  }) =>
      Future.value();

  @override
  Future<void> stop() => Future.value();
}

Map<String, Object?> _turnCreated({
  required int sequence,
  String turnId = 'turn-1234',
}) =>
    {
      'protocolVersion': 4,
      'event': 'turnCreated',
      'sessionId': _sessionId,
      'turnId': turnId,
      'sequence': sequence,
      'revision': 0,
      'requestId': 'request-$turnId',
    };

AvaValidatedSvgCanvasDocument _validatedDocument() {
  final available = _listFixture('ava_realtime_canvas_events_v4.json').first;
  final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson(available);
  return AvaValidatedSvgCanvasDocument.verify(
    descriptor: descriptor,
    svgBytes:
        File('protocol/fixtures/ava_svg_document_v4.svg').readAsBytesSync(),
    manifestBytes: Uint8List.fromList(utf8.encode(_manifestJson)),
  );
}

Map<String, Object?> _objectFixture(String name) {
  final decoded =
      jsonDecode(File('protocol/fixtures/$name').readAsStringSync());
  return (decoded as Map).cast<String, Object?>();
}

List<Map<String, Object?>> _listFixture(String name) {
  final decoded =
      jsonDecode(File('protocol/fixtures/$name').readAsStringSync());
  return [
    for (final event in decoded as List) (event as Map).cast<String, Object?>(),
  ];
}
