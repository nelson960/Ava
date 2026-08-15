import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:ava/canvas/ava_canvas_engine.dart';
import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/services/ava_realtime_presentation.dart';
import 'package:ava/services/ava_realtime_session.dart';
import 'package:ava/services/ava_realtime_turn_runtime.dart';
import 'package:ava/services/ava_realtime_webrtc.dart';
import 'package:ava/services/pcm_speech_output_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'keyless v3 runtime plays PCM and drives clocked presentation',
    () async {
      final transport = _PushRealtimeTransport();
      final pcm = _ControlledPcmOutput();
      final canvas = AvaCanvasEngine();
      final presentation = AvaRealtimePresentationController();
      final runtime = _runtime(
        transport: transport,
        pcm: pcm,
        canvas: canvas,
        presentation: presentation,
      );

      final run = runtime.run();
      await _waitFor(
        () => transport.sentEvents.length == 1,
        'clientReady was not sent',
      );
      final events = _events();
      for (final event in events.take(13)) {
        transport.add(AvaRealtimeControlFrame(event));
        if (event.type == AvaRealtimeServerEventType.audioChunk) {
          transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
        }
      }

      await _waitFor(
        () => pcm.audio.length == 16 && pcm.finishCalled,
        'PCM was not appended and finished',
      );
      pcm.progressValue = const PcmPlaybackProgress(
        renderedSampleCount: 4,
        scheduledSampleCount: 8,
        sampleRateHz: 24000,
      );
      await runtime.synchronizePlayback();

      expect(
        presentation.state.caption,
        'A plant cell is a tiny living system',
      );
      expect(
        presentation.state.lipSync!.cues.single.viseme,
        AvaRealtimeViseme.aa,
      );
      expect(canvas.scene?.id, 'plant_cell_scene');
      expect(canvas.panelVisible, isTrue);

      pcm.completePlayback(8);
      await _waitFor(
        () => transport.sentEvents
            .where(
              (event) =>
                  event.type == AvaRealtimeClientEventType.playbackProgress,
            )
            .any(
              (event) =>
                  event.renderedSampleCount == 8 &&
                  event.bufferedSampleCount == 0,
            ),
        'final playback progress was not reported',
      );

      transport.add(AvaRealtimeControlFrame(events.last));
      await _waitFor(() => runtime.isTerminal, 'turn did not complete');
      await runtime.close();
      await run;

      expect(runtime.snapshot.phase, AvaRealtimeTurnPhase.completed);
      expect(presentation.state.phase, AvaRealtimeTurnPhase.completed);
      expect(presentation.state.caption, isNull);
      expect(presentation.state.lipSync, isNull);
      expect(presentation.state.deliveredText, events.last.deliveredText);
      expect(
        transport.sentEvents.map((event) => event.type),
        [
          AvaRealtimeClientEventType.clientReady,
          AvaRealtimeClientEventType.playbackStarted,
          AvaRealtimeClientEventType.playbackProgress,
          AvaRealtimeClientEventType.playbackProgress,
        ],
      );
    },
  );

  test('persistent runtime carries two turns over one media connection',
      () async {
    final transport = _PushRealtimeTransport();
    final pcm = _MultiTurnPcmOutput();
    final media = DeterministicAvaRealtimeMediaSession(
      offerSdp: _deterministicOfferSdp,
    );
    final presentation = AvaRealtimePresentationController();
    final runtime = AvaRealtimeTurnRuntime(
      sessionId: 'session-1234',
      limits: AvaSessionBootstrapV3.fromJson(
        _objectFixture('ava_session_bootstrap_v3.json'),
      ).limits,
      mediaSession: media,
      transport: transport,
      speechOutput: pcm,
      canvasEngine: AvaCanvasEngine(),
      presentation: presentation,
      volume: 0.8,
      cancelRequestIdFactory: () => 'cancel-persistent-runtime',
      automaticPlaybackPolling: false,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'persistent clientReady was not sent',
    );

    Future<void> deliverTurn(String turnId, String streamId) async {
      final events = _eventsForTurn(turnId: turnId, streamId: streamId);
      for (final event in events.take(events.length - 1)) {
        transport.add(AvaRealtimeControlFrame(event));
        if (event.type == AvaRealtimeServerEventType.audioChunk) {
          transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
        }
      }
      await _waitFor(
        () => transport.sentEvents.any(
          (event) =>
              event.type == AvaRealtimeClientEventType.playbackProgress &&
              event.turnId == turnId &&
              event.renderedSampleCount == 8,
        ),
        'persistent turn did not publish final playback progress',
      );
      transport.add(AvaRealtimeControlFrame(events.last));
      await _waitFor(
        () =>
            presentation.state.turnId == turnId &&
            presentation.state.phase == AvaRealtimeTurnPhase.completed,
        'persistent turn did not complete',
      );
    }

    await deliverTurn('turn-1234', 'stream-1234');

    transport.add(
      AvaRealtimeControlFrame(
        AvaRealtimeServerEvent.fromJson({
          'protocolVersion': 3,
          'event': 'interactionRejected',
          'sessionId': 'session-1234',
          'turnId': 'turn-1234',
          'sequence': 14,
          'revision': 6,
          'sceneId': 'plant_cell_scene',
          'sceneRevision': 1,
          'interactionRevision': 2,
          'bindingId': 'select_nucleus',
          'rejectionCode': 'interactionRevisionStale',
          'message': 'Canvas interaction revision is stale.',
        }),
      ),
    );
    await _waitFor(
      () => presentation.state.issues.any(
        (issue) =>
            issue.code == AvaRealtimePresentationIssueCode.interactionRejected,
      ),
      'session-scoped interaction rejection was not presented',
    );
    expect(runtime.isTerminal, isTrue);
    expect(
      await runtime.reportCanvasInteraction(
        const AvaCanvasInteractionOutcome(
          sceneId: 'plant_cell_scene',
          sceneRevision: 1,
          interactionRevision: 3,
          bindingId: 'select_nucleus',
          action: 'select',
        ),
      ),
      isTrue,
    );
    await deliverTurn('turn-5678', 'stream-5678');

    expect(pcm.startedTurnIds, ['turn-1234', 'turn-5678']);
    expect(
      transport.sentEvents
          .where(
            (event) => event.type == AvaRealtimeClientEventType.clientReady,
          )
          .length,
      1,
    );
    expect(media.microphoneChangeCount, 2);
    expect(media.microphoneEnabled, isTrue);
    await runtime.stopConversation(reason: 'userTurnedMicrophoneOff');
    await run;
    expect(media.closed, isTrue);
    expect(media.microphoneEnabled, isFalse);
  });

  test('persistent runtime rejects a server close after a completed turn',
      () async {
    final transport = _PushRealtimeTransport();
    final pcm = _MultiTurnPcmOutput();
    final runtime = AvaRealtimeTurnRuntime(
      sessionId: 'session-1234',
      limits: AvaSessionBootstrapV3.fromJson(
        _objectFixture('ava_session_bootstrap_v3.json'),
      ).limits,
      mediaSession: DeterministicAvaRealtimeMediaSession(
        offerSdp: _deterministicOfferSdp,
      ),
      transport: transport,
      speechOutput: pcm,
      canvasEngine: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
      volume: 0.8,
      cancelRequestIdFactory: () => 'cancel-persistent-close',
      automaticPlaybackPolling: false,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'persistent clientReady was not sent',
    );
    final events = _eventsForTurn(
      turnId: 'turn-1234',
      streamId: 'stream-1234',
    );
    for (final event in events.take(events.length - 1)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'turn did not publish final playback progress',
    );
    transport.add(AvaRealtimeControlFrame(events.last));
    await transport.finish();

    await expectLater(
      run,
      throwsA(
        isA<AvaRealtimeRuntimeException>().having(
          (error) => error.message,
          'message',
          contains('after a turn'),
        ),
      ),
    );
    await runtime.close();
  });

  test('normal transport close preserves the preceding server turn failure',
      () async {
    final transport = _PushRealtimeTransport();
    final presentation = AvaRealtimePresentationController();
    final runtime = _runtime(
      transport: transport,
      pcm: _ControlledPcmOutput(),
      canvas: AvaCanvasEngine(),
      presentation: presentation,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final payload in const [
      {
        'protocolVersion': 3,
        'event': 'turnCreated',
        'sessionId': 'session-1234',
        'turnId': 'turn-failed-close',
        'sequence': 0,
        'revision': 0,
        'requestId': 'request-failed-close',
      },
      {
        'protocolVersion': 3,
        'event': 'turnPhaseChanged',
        'sessionId': 'session-1234',
        'turnId': 'turn-failed-close',
        'sequence': 1,
        'revision': 1,
        'phase': 'connecting',
      },
      {
        'protocolVersion': 3,
        'event': 'turnPhaseChanged',
        'sessionId': 'session-1234',
        'turnId': 'turn-failed-close',
        'sequence': 2,
        'revision': 2,
        'phase': 'listening',
      },
      {
        'protocolVersion': 3,
        'event': 'turnFailed',
        'sessionId': 'session-1234',
        'turnId': 'turn-failed-close',
        'sequence': 3,
        'revision': 3,
        'failure': {
          'failureId': 'failure-provider-timeout',
          'component': 'realtimeProvider',
          'code': 'modelUnavailable',
          'message': 'Realtime active response timed out.',
          'retryable': true,
        },
        'deliveredSampleCount': 0,
        'deliveredText': '',
      },
    ]) {
      transport.add(
        AvaRealtimeControlFrame(AvaRealtimeServerEvent.fromJson(payload)),
      );
    }
    await _waitFor(() => runtime.isTerminal, 'turn failure was not received');
    await transport.finish();

    await expectLater(
      run,
      throwsA(
        isA<AvaRealtimeRuntimeException>().having(
          (error) => error.message,
          'message',
          'Realtime active response timed out.',
        ),
      ),
    );
    expect(presentation.state.phase, AvaRealtimeTurnPhase.failed);
    expect(presentation.state.failureId, 'failure-provider-timeout');
    await runtime.close();
  });

  test('microphone pause and resume retain one negotiated media session',
      () async {
    final transport = _PushRealtimeTransport();
    final media = DeterministicAvaRealtimeMediaSession(
      offerSdp: _deterministicOfferSdp,
    );
    final runtime = AvaRealtimeTurnRuntime(
      sessionId: 'session-1234',
      limits: AvaSessionBootstrapV3.fromJson(
        _objectFixture('ava_session_bootstrap_v3.json'),
      ).limits,
      mediaSession: media,
      transport: transport,
      speechOutput: _ControlledPcmOutput(),
      canvasEngine: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
      volume: 0.8,
      cancelRequestIdFactory: () => 'cancel-paused-runtime',
      automaticPlaybackPolling: false,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final event in _events().take(3)) {
      transport.add(AvaRealtimeControlFrame(event));
    }
    await _waitFor(
      () => media.microphoneEnabled,
      'microphone was not enabled for listening',
    );

    await runtime.pauseConversation();
    expect(media.microphoneEnabled, isFalse);
    expect(
      transport.sentEvents.where(
        (event) => event.type == AvaRealtimeClientEventType.cancelTurn,
      ),
      isEmpty,
    );

    await runtime.resumeConversation();
    expect(media.microphoneEnabled, isTrue);
    expect(
      transport.sentEvents
          .where(
            (event) => event.type == AvaRealtimeClientEventType.clientReady,
          )
          .length,
      1,
    );

    await runtime.close();
    await run;
    expect(media.closed, isTrue);
  });

  test('barge-in reports exact stopped prefix and cancels idempotently',
      () async {
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput(stopAtSample: 3);
    final media = DeterministicAvaRealtimeMediaSession(
      offerSdp: _deterministicOfferSdp,
    );
    final presentation = AvaRealtimePresentationController();
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: AvaCanvasEngine(),
      presentation: presentation,
      mediaSession: media,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final event in _events().take(13)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');

    await runtime.interrupt();
    expect(media.microphoneEnabled, isTrue);
    await runtime.interrupt();
    expect(
      transport.sentEvents
          .where(
            (event) => event.type == AvaRealtimeClientEventType.playbackStopped,
          )
          .single
          .renderedSampleCount,
      3,
    );
    expect(
      transport.sentEvents
          .where(
            (event) => event.type == AvaRealtimeClientEventType.cancelTurn,
          )
          .single
          .requestId,
      'cancel-runtime-1',
    );

    transport.add(
      AvaRealtimeControlFrame(
        AvaRealtimeServerEvent.fromJson({
          'protocolVersion': 3,
          'event': 'turnPhaseChanged',
          'sessionId': 'session-1234',
          'turnId': 'turn-1234',
          'sequence': 13,
          'revision': 6,
          'phase': 'cancelling',
        }),
      ),
    );
    transport.add(
      AvaRealtimeControlFrame(
        AvaRealtimeServerEvent.fromJson({
          'protocolVersion': 3,
          'event': 'turnCancelled',
          'sessionId': 'session-1234',
          'turnId': 'turn-1234',
          'sequence': 14,
          'revision': 7,
          'deliveredSampleCount': 3,
          'deliveredText': 'A',
          'reason': 'childStartedSpeaking',
        }),
      ),
    );
    await _waitFor(() => runtime.isTerminal, 'cancelled turn did not finish');
    await runtime.close();
    await run;

    expect(runtime.snapshot.phase, AvaRealtimeTurnPhase.cancelled);
    expect(presentation.state.phase, AvaRealtimeTurnPhase.cancelled);
    expect(presentation.state.deliveredText, 'A');
  });

  test('only semantic canvas outcomes cross the realtime transport', () async {
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput();
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    final events = _events();
    for (final event in events.take(13)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');

    final localOnly = await runtime.reportCanvasInteraction(
      const AvaCanvasInteractionOutcome(
        sceneId: 'plant_cell_scene',
        sceneRevision: 1,
        interactionRevision: 1,
        bindingId: 'toggle_nucleus',
        action: 'toggleVisibility',
      ),
    );
    expect(localOnly, isFalse);

    final explore = await runtime.reportCanvasInteraction(
      const AvaCanvasInteractionOutcome(
        sceneId: 'plant_cell_scene',
        sceneRevision: 1,
        interactionRevision: 2,
        bindingId: 'explore_nucleus',
        action: 'explore',
      ),
    );
    expect(explore, isTrue);
    final exploreReport = transport.sentEvents.last;
    expect(exploreReport.type, AvaRealtimeClientEventType.canvasInteraction);
    expect(exploreReport.bindingId, 'explore_nucleus');

    final semantic = await runtime.reportCanvasInteraction(
      const AvaCanvasInteractionOutcome(
        sceneId: 'plant_cell_scene',
        sceneRevision: 1,
        interactionRevision: 3,
        bindingId: 'select_nucleus',
        action: 'select',
      ),
    );
    expect(semantic, isTrue);
    final report = transport.sentEvents.last;
    expect(report.type, AvaRealtimeClientEventType.canvasInteraction);
    expect(report.sceneId, 'plant_cell_scene');
    expect(report.sceneRevision, 1);
    expect(report.interactionRevision, 3);
    expect(report.bindingId, 'select_nucleus');
    expect(report.toJson().keys, {
      'protocolVersion',
      'event',
      'sessionId',
      'sequence',
      'sceneId',
      'sceneRevision',
      'interactionRevision',
      'bindingId',
    });

    pcm.completePlayback(8);
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'final playback progress was not reported',
    );
    transport.add(AvaRealtimeControlFrame(events.last));
    await _waitFor(() => runtime.isTerminal, 'turn did not complete');
    await runtime.close();
    await run;
  });

  test('binary PCM must immediately and exactly match its descriptor',
      () async {
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput();
    final media = DeterministicAvaRealtimeMediaSession(
      offerSdp: _deterministicOfferSdp,
    );
    final presentation = AvaRealtimePresentationController();
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: AvaCanvasEngine(),
      presentation: presentation,
      mediaSession: media,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final event in _events().take(10)) {
      transport.add(AvaRealtimeControlFrame(event));
    }
    transport.add(AvaRealtimePcmFrame(Uint8List(6)));
    await transport.finish();

    await expectLater(
      run,
      throwsA(
        isA<AvaRealtimeRuntimeException>().having(
          (error) => error.message,
          'message',
          contains('does not match descriptor'),
        ),
      ),
    );
    expect(presentation.state.phase, AvaRealtimeTurnPhase.failed);
    expect(pcm.stopCalled, isTrue);
    expect(media.microphoneEnabled, isFalse);
  });

  test('a rejected optional canvas stays closed while speech completes',
      () async {
    final rawEvents = _eventPayloads();
    final cue = rawEvents.firstWhere((event) => event['event'] == 'canvasCue');
    final transaction = (cue['transaction']! as Map).cast<String, Object?>();
    transaction['baseRevision'] = 2;
    transaction['revision'] = 3;
    final events = [
      for (final payload in rawEvents) AvaRealtimeServerEvent.fromJson(payload),
    ];
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput();
    final canvas = AvaCanvasEngine();
    final presentation = AvaRealtimePresentationController();
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: canvas,
      presentation: presentation,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final event in events.take(13)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');
    pcm.completePlayback(8);
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'final playback progress was not reported',
    );
    transport.add(AvaRealtimeControlFrame(events.last));
    await _waitFor(() => runtime.isTerminal, 'turn did not complete');
    await runtime.close();
    await run;

    expect(canvas.panelVisible, isFalse);
    expect(canvas.scene, isNull);
    expect(
      presentation.state.issues
          .where(
            (issue) =>
                issue.code == AvaRealtimePresentationIssueCode.canvasRejected,
          )
          .length,
      1,
    );
    expect(presentation.state.phase, AvaRealtimeTurnPhase.completed);
  });

  test('canvas rejection never changes the persistent microphone track',
      () async {
    final payloads = _eventPayloads();
    final cueIndex = payloads.indexWhere(
      (event) => event['event'] == 'canvasCue',
    );
    final cue = payloads[cueIndex];
    payloads[cueIndex] = {
      'protocolVersion': 3,
      'event': 'canvasRejected',
      'sessionId': cue['sessionId'],
      'turnId': cue['turnId'],
      'sequence': cue['sequence'],
      'revision': cue['revision'],
      'failure': {
        'failureId': 'failure-runtime-canvas',
        'component': 'canvasSpecialist',
        'code': 'canvasRejected',
        'message': 'The optional visual did not pass validation.',
        'retryable': false,
      },
    };
    final events = [
      for (final payload in payloads) AvaRealtimeServerEvent.fromJson(payload),
    ];
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput();
    final canvas = AvaCanvasEngine();
    final presentation = AvaRealtimePresentationController();
    final media = DeterministicAvaRealtimeMediaSession(
      offerSdp: _deterministicOfferSdp,
    );
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: canvas,
      presentation: presentation,
      mediaSession: media,
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final event in events.take(13)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');
    pcm.completePlayback(8);
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'final playback progress was not reported',
    );
    transport.add(AvaRealtimeControlFrame(events.last));
    await _waitFor(() => runtime.isTerminal, 'turn did not complete');

    expect(media.microphoneEnabled, isTrue);
    expect(canvas.panelVisible, isFalse);
    expect(presentation.state.phase, AvaRealtimeTurnPhase.completed);
    expect(
      presentation.state.issues.single.code,
      AvaRealtimePresentationIssueCode.canvasRejected,
    );

    await runtime.close();
    await run;
  });

  test('a validated canvas arriving after audio completion still opens',
      () async {
    final payloads = _eventPayloads();
    final cueIndex = payloads.indexWhere(
      (event) => event['event'] == 'canvasCue',
    );
    final cue = payloads.removeAt(cueIndex);
    final terminal = payloads.removeLast();
    cue['revision'] = 5;
    payloads.add(cue);
    payloads.add(terminal);
    for (var index = 0; index < payloads.length; index += 1) {
      payloads[index]['sequence'] = index;
    }
    final events = [
      for (final payload in payloads) AvaRealtimeServerEvent.fromJson(payload),
    ];
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput();
    final canvas = AvaCanvasEngine();
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: canvas,
      presentation: AvaRealtimePresentationController(),
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    for (final event in events.take(12)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');
    pcm.completePlayback(8);
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'final playback progress was not reported',
    );
    expect(canvas.panelVisible, isFalse);

    transport.add(AvaRealtimeControlFrame(events[12]));
    await _waitFor(() => canvas.panelVisible, 'late canvas did not open');
    expect(canvas.scene?.id, 'plant_cell_scene');

    transport.add(AvaRealtimeControlFrame(events.last));
    await _waitFor(() => runtime.isTerminal, 'turn did not complete');
    await runtime.close();
    await run;
  });

  test('presentation state cannot leak delivered data into the next turn', () {
    final presentation = AvaRealtimePresentationController();
    final events = _events();
    presentation.beginTurn(
      sessionId: 'session-1234',
      turnId: 'turn-1234',
    );
    presentation.addSegment(0, events[4].segment!);
    presentation.complete(events.last);
    expect(presentation.state.deliveredText, isNotNull);

    presentation.beginTurn(
      sessionId: 'session-5678',
      turnId: 'turn-5678',
    );
    expect(presentation.state.currentSegment, isNull);
    expect(presentation.state.deliveredText, isNull);
    expect(presentation.state.failureMessage, isNull);
    expect(presentation.state.renderedSampleCount, 0);
    expect(presentation.state.receivedSampleCount, 0);
  });

  test('a progress read crossing native completion is not published twice',
      () async {
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput(delayProgress: true);
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    final events = _events();
    for (final event in events.take(13)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');

    final crossedPoll = runtime.synchronizePlayback();
    await pcm.progressRequested;
    pcm.completePlayback(8);
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'finish did not publish terminal progress',
    );
    pcm.releaseProgress();
    await crossedPoll;

    expect(
      transport.sentEvents
          .where(
            (event) =>
                event.type == AvaRealtimeClientEventType.playbackProgress &&
                event.renderedSampleCount == 8,
          )
          .length,
      1,
    );

    transport.add(AvaRealtimeControlFrame(events.last));
    await _waitFor(() => runtime.isTerminal, 'turn did not complete');
    await runtime.close();
    await run;
  });

  test('a poll cannot publish the terminal sample before native finish',
      () async {
    final transport = _PushRealtimeTransport();
    final pcm = _ControlledPcmOutput();
    final runtime = _runtime(
      transport: transport,
      pcm: pcm,
      canvas: AvaCanvasEngine(),
      presentation: AvaRealtimePresentationController(),
    );

    final run = runtime.run();
    await _waitFor(
      () => transport.sentEvents.length == 1,
      'clientReady was not sent',
    );
    final events = _events();
    for (final event in events.take(13)) {
      transport.add(AvaRealtimeControlFrame(event));
      if (event.type == AvaRealtimeServerEventType.audioChunk) {
        transport.add(AvaRealtimePcmFrame(Uint8List(event.byteLength!)));
      }
    }
    await _waitFor(() => pcm.finishCalled, 'native finish did not start');

    pcm.progressValue = const PcmPlaybackProgress(
      renderedSampleCount: 8,
      scheduledSampleCount: 8,
      sampleRateHz: 24000,
    );
    await runtime.synchronizePlayback();
    expect(
      transport.sentEvents.where(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      isEmpty,
    );

    pcm.completePlayback(8);
    await _waitFor(
      () => transport.sentEvents.any(
        (event) =>
            event.type == AvaRealtimeClientEventType.playbackProgress &&
            event.renderedSampleCount == 8,
      ),
      'finish did not publish terminal progress',
    );
    expect(
      transport.sentEvents
          .where(
            (event) =>
                event.type == AvaRealtimeClientEventType.playbackProgress &&
                event.renderedSampleCount == 8,
          )
          .length,
      1,
    );

    transport.add(AvaRealtimeControlFrame(events.last));
    await _waitFor(() => runtime.isTerminal, 'turn did not complete');
    await runtime.close();
    await run;
  });

  test('caption presentation replaces bounded lines instead of growing text',
      () {
    const speech = 'Water warms up and rises. Then it cools into clouds.';

    expect(captionLineAt(speech, 0), 'Water warms up and rises.');
    expect(captionLineAt(speech, 27), 'Then it cools into clouds.');
    expect(
      captionLineAt(
        'One two three four five six seven eight nine ten.',
        0,
        maximumScalarCount: 20,
      ),
      'One two three four',
    );
  });

  test('caption-preview EE is a recognized viseme', () {
    final presentation = AvaRealtimePresentationController();
    presentation.beginTurn(
      sessionId: 'session-1234',
      turnId: 'turn-1234',
    );
    presentation.addSegment(
      0,
      AvaRealtimeTurnSegment.fromJson({
        'id': 'speech',
        'speech': 'See',
        'delivery': 'neutral',
        'expression': 'neutral',
        'gaze': 'child',
        'motionIntent': 'none',
        'canvasCues': <Object>[],
      }),
    );
    presentation.addAlignmentSpans([
      AvaRealtimeAlignmentSpan.fromJson({
        'segmentId': 'speech',
        'textStart': 0,
        'textEnd': 3,
        'sampleStart': 0,
        'sampleEnd': 2400,
        'phoneme': 'EE',
      }),
    ]);
    presentation.updatePlaybackClock(
      renderedSampleCount: 1200,
      receivedSampleCount: 2400,
    );

    expect(
        presentation.state.lipSync!.cues.single.viseme, AvaRealtimeViseme.ee);
    expect(presentation.state.issues, isEmpty);
  });
}

AvaRealtimeTurnRuntime _runtime({
  required _PushRealtimeTransport transport,
  required _ControlledPcmOutput pcm,
  required AvaCanvasEngine canvas,
  required AvaRealtimePresentationController presentation,
  AvaRealtimeMediaSession? mediaSession,
}) {
  final bootstrap = AvaSessionBootstrapV3.fromJson(
    _objectFixture('ava_session_bootstrap_v3.json'),
  );
  return AvaRealtimeTurnRuntime(
    sessionId: bootstrap.sessionId,
    limits: bootstrap.limits,
    mediaSession: mediaSession ??
        DeterministicAvaRealtimeMediaSession(
          offerSdp: _deterministicOfferSdp,
        ),
    transport: transport,
    speechOutput: pcm,
    canvasEngine: canvas,
    presentation: presentation,
    volume: 0.8,
    cancelRequestIdFactory: () => 'cancel-runtime-1',
    automaticPlaybackPolling: false,
  );
}

List<AvaRealtimeServerEvent> _eventsForTurn({
  required String turnId,
  required String streamId,
}) {
  return [
    for (final original in _eventPayloads())
      AvaRealtimeServerEvent.fromJson({
        ...original,
        'turnId': turnId,
        if (original.containsKey('streamId')) 'streamId': streamId,
        if (original['event'] == 'turnCreated') 'requestId': 'request-$turnId',
      }),
  ];
}

const _deterministicOfferSdp = 'v=0\r\n'
    'o=- 1000 2 IN IP4 127.0.0.1\r\n'
    's=Ava test offer\r\n'
    't=0 0\r\n'
    'm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n'
    'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n';

final class _PushRealtimeTransport implements AvaRealtimeTransport {
  final StreamController<AvaRealtimeInboundFrame> _controller =
      StreamController();
  final List<AvaRealtimeClientEvent> sentEvents = [];
  bool _closed = false;

  void add(AvaRealtimeInboundFrame frame) {
    if (_closed) throw StateError('Fake realtime transport is closed.');
    _controller.add(frame);
  }

  Future<void> finish() => _controller.close();

  @override
  Stream<AvaRealtimeInboundFrame> frames() => _controller.stream;

  @override
  Future<void> send(AvaRealtimeClientEvent event) async {
    if (_closed) throw StateError('Fake realtime transport is closed.');
    sentEvents.add(event);
  }

  @override
  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    await _controller.close();
  }
}

final class _ControlledPcmOutput implements PcmSpeechOutputService {
  _ControlledPcmOutput({this.stopAtSample = 0, this.delayProgress = false});

  final int stopAtSample;
  final bool delayProgress;
  final List<int> audio = [];
  final Completer<PcmStopResult> _finish = Completer();
  final Completer<void> _progressRequested = Completer();
  final Completer<PcmPlaybackProgress> _progressResult = Completer();
  PcmPlaybackProgress progressValue = const PcmPlaybackProgress(
    renderedSampleCount: 0,
    scheduledSampleCount: 0,
    sampleRateHz: 24000,
  );
  bool started = false;
  bool finishCalled = false;
  bool stopCalled = false;

  Future<void> get progressRequested => _progressRequested.future;

  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {
    started = true;
  }

  @override
  Future<void> append({
    required String turnId,
    required Uint8List audio,
  }) async {
    if (!started) throw StateError('PCM append occurred before start.');
    this.audio.addAll(audio);
    progressValue = PcmPlaybackProgress(
      renderedSampleCount: progressValue.renderedSampleCount,
      scheduledSampleCount: this.audio.length ~/ 2,
      sampleRateHz: 24000,
    );
  }

  @override
  Future<PcmStopResult> finish({required String turnId}) {
    finishCalled = true;
    return _finish.future;
  }

  void completePlayback(int renderedSampleCount) {
    progressValue = PcmPlaybackProgress(
      renderedSampleCount: renderedSampleCount,
      scheduledSampleCount: audio.length ~/ 2,
      sampleRateHz: 24000,
    );
    if (!_finish.isCompleted) {
      _finish.complete(
        PcmStopResult(renderedSampleCount: renderedSampleCount),
      );
    }
  }

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async {
    if (!delayProgress) return progressValue;
    if (!_progressRequested.isCompleted) _progressRequested.complete();
    return _progressResult.future;
  }

  void releaseProgress() {
    if (!_progressResult.isCompleted) _progressResult.complete(progressValue);
  }

  @override
  Future<Duration> position({required String turnId}) async =>
      progressValue.position;

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async {
    if (!_finish.isCompleted) {
      _finish.completeError(StateError('speech interrupted'));
    }
    return PcmStopResult(renderedSampleCount: stopAtSample);
  }

  @override
  Future<void> stop() async {
    stopCalled = true;
    if (finishCalled && !_finish.isCompleted) {
      _finish.completeError(StateError('speech stopped'));
    }
  }
}

final class _MultiTurnPcmOutput implements PcmSpeechOutputService {
  final List<String> startedTurnIds = [];
  final Map<String, int> _scheduledSamples = {};

  @override
  Future<void> start({
    required String turnId,
    required int sampleRateHz,
    required double volume,
  }) async {
    startedTurnIds.add(turnId);
    _scheduledSamples[turnId] = 0;
  }

  @override
  Future<void> append({
    required String turnId,
    required Uint8List audio,
  }) async {
    final current = _scheduledSamples[turnId];
    if (current == null) throw StateError('PCM append occurred before start.');
    _scheduledSamples[turnId] = current + audio.length ~/ 2;
  }

  @override
  Future<PcmStopResult> finish({required String turnId}) async =>
      PcmStopResult(renderedSampleCount: _samples(turnId));

  @override
  Future<PcmPlaybackProgress> progress({required String turnId}) async =>
      PcmPlaybackProgress(
        renderedSampleCount: _samples(turnId),
        scheduledSampleCount: _samples(turnId),
        sampleRateHz: 24000,
      );

  @override
  Future<Duration> position({required String turnId}) async =>
      Duration(microseconds: (_samples(turnId) * 1000000) ~/ 24000);

  @override
  Future<PcmStopResult> fadeOutAndStop({
    required String turnId,
    Duration fadeDuration = const Duration(milliseconds: 80),
  }) async =>
      PcmStopResult(renderedSampleCount: _samples(turnId));

  @override
  Future<void> stop() async {}

  int _samples(String turnId) {
    final value = _scheduledSamples[turnId];
    if (value == null) throw StateError('Unknown PCM turn $turnId.');
    return value;
  }
}

Future<void> _waitFor(
  bool Function() condition,
  String failureMessage,
) async {
  for (var attempt = 0; attempt < 100; attempt += 1) {
    if (condition()) return;
    await Future<void>.delayed(Duration.zero);
  }
  fail(failureMessage);
}

Map<String, Object?> _objectFixture(String name) {
  final decoded = jsonDecode(
    File('protocol/fixtures/$name').readAsStringSync(),
  );
  return (decoded as Map).cast<String, Object?>();
}

List<Map<String, Object?>> _eventPayloads() {
  final decoded = jsonDecode(
    File('protocol/fixtures/ava_realtime_events_v3.json').readAsStringSync(),
  );
  return [
    for (final event in decoded as List) (event as Map).cast<String, Object?>(),
  ];
}

List<AvaRealtimeServerEvent> _events() => [
      for (final payload in _eventPayloads())
        AvaRealtimeServerEvent.fromJson(payload),
    ];
