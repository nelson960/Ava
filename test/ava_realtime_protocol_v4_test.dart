import 'dart:convert';
import 'dart:io';

import 'package:ava/models/ava_realtime_protocol_v4.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('protocol-v4 bootstrap is strict and retains separate credentials', () {
    final fixture = _objectFixture('ava_session_bootstrap_v4.json');
    final bootstrap = AvaSessionBootstrapV4.fromJson(fixture);

    expect(bootstrap.sessionId, 'session-1234');
    expect(bootstrap.canvasToken, 'canvas-token-value');
    expect(bootstrap.canvasDocumentBaseUrl, contains('/canvas-documents'));
    expect(bootstrap.limits.maxSvgBytes, 262144);
    expect(bootstrap.limits.maxAudioChunkBytes, 192000);

    expect(
      () => AvaSessionBootstrapV4.fromJson({...fixture, 'unknown': true}),
      throwsFormatException,
    );
  });

  test('v4 core events expose the native speech contract', () {
    final payload =
        _jsonLineFixtures('ava_realtime_speech_events_v4.jsonl').first;
    final v4 = AvaRealtimeServerEventV4.fromJson({...payload, 'sequence': 17});

    expect(v4.type, AvaRealtimeServerEventTypeV4.turnCreated);
    expect(v4.sequence, 17);
    expect(v4.turnId, payload['turnId']);
    expect(v4.revision, payload['revision']);
  });

  test('v4 speech rejects every legacy primitive canvas payload', () {
    final segmentReady =
        _jsonLineFixtures('ava_realtime_speech_events_v4.jsonl')[2];
    final segment = Map<String, Object?>.from(
      segmentReady['segment']! as Map,
    )..['canvasCues'] = [
        {'legacy': 'typed'},
      ];
    expect(
      () => AvaRealtimeServerEventV4.fromJson({
        ...segmentReady,
        'segment': segment,
      }),
      throwsA(
        isA<FormatException>().having(
          (error) => error.message,
          'message',
          contains('cannot contain canvas cues'),
        ),
      ),
    );
  });

  test('client v4 constructs speech controls without legacy conversion', () {
    final translated = AvaRealtimeClientEventV4.clientReady(
      sessionId: 'session-1234',
      sequence: 0,
    ).toJson();
    expect(translated['protocolVersion'], 4);
    expect(translated['sequence'], 0);
    expect(translated['rendererCapabilityVersion'], 'ava-svg-renderer-v2');

    final playback = AvaRealtimeClientEventV4.playbackStarted(
      sessionId: 'session-1234',
      sequence: 1,
      turnId: 'turn-1234',
      streamId: 'stream-1234',
    ).toJson();
    expect(playback['protocolVersion'], 4);
    expect(playback['renderedSampleCount'], 0);
  });

  test('client v4 render-profile update is strict and session-scoped', () {
    final profile = AvaCanvasRenderProfileV4.fromJson({
      'usableAspectRatio': 1.6,
      'orientation': 'landscape',
      'widthClass': 'regular',
      'heightClass': 'compact',
      'safeInsets': {
        'top': 0.02,
        'right': 0.03,
        'bottom': 0.04,
        'left': 0.03,
      },
      'touchEnvironment': 'direct',
      'minimumInteractionTargetPoints': 44.0,
      'textScaleFactor': 1.15,
    });
    final update = AvaRealtimeClientEventV4.canvasRenderProfileUpdated(
      sessionId: 'session-1234',
      sequence: 1,
      profileRevision: 7,
      renderProfile: profile,
    ).toJson();

    expect(update['event'], 'canvasRenderProfileUpdated');
    expect(update['profileRevision'], 7);
    expect(update, isNot(contains('turnId')));
    expect(
      (update['renderProfile']! as Map)['usableAspectRatio'],
      1.6,
    );
    expect(
      () => AvaCanvasRenderProfileV4.fromJson({
        ...profile.toJson(),
        'orientation': 'portrait',
      }),
      throwsFormatException,
    );
  });

  test('canvas rejection identifies a call before generation exists', () {
    final event = AvaRealtimeServerEventV4.fromJson({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-1234',
      'sequence': 0,
      'revision': 1,
      'callId': 'call-1234',
      'sourceTurnId': 'turn-1234',
      'rejectionCode': 'providerInvalid',
      'message': 'The optional drawing request was rejected.',
      'retryable': false,
    });
    expect(event.type, AvaRealtimeServerEventTypeV4.canvasRejected);
    expect(event.toJson()['callId'], 'call-1234');
  });

  test('canvas generation limit is a structured rejection', () {
    final event = AvaRealtimeServerEventV4.fromJson({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-1234',
      'sequence': 1,
      'revision': 1,
      'generationId': 'generation-1234',
      'sourceTurnId': 'turn-1234',
      'rejectionCode': 'generationLimitExceeded',
      'message': 'The canvas illustrator exceeded its generation limit.',
      'retryable': true,
    });

    expect(event.type, AvaRealtimeServerEventTypeV4.canvasRejected);
    expect(event.toJson()['rejectionCode'], 'generationLimitExceeded');
  });

  test('canvas timeout is a structured rejection with child message', () {
    final event = AvaRealtimeServerEventV4.fromJson({
      'protocolVersion': 4,
      'event': 'canvasRejected',
      'sessionId': 'session-1234',
      'sequence': 1,
      'revision': 1,
      'generationId': 'generation-1234',
      'sourceTurnId': 'turn-1234',
      'rejectionCode': 'timeout',
      'message': 'That drawing took too long. Try again?',
      'retryable': true,
    });

    expect(event.type, AvaRealtimeServerEventTypeV4.canvasRejected);
    expect(event.toJson()['rejectionCode'], 'timeout');
    expect(event.toJson()['message'], 'That drawing took too long. Try again?');
    expect(event.toJson()['retryable'], isTrue);
  });

  test('canvas generation start is a strict session-scoped control', () {
    final event = AvaRealtimeServerEventV4.fromJson(
      _objectFixture('ava_realtime_canvas_generation_v4.json'),
    );

    expect(event.type, AvaRealtimeServerEventTypeV4.canvasGenerationStarted);
    expect(event.isCanvas, isTrue);
    expect(event.toJson()['operation'], 'new');
    expect(
      AvaRealtimeServerEventV4.fromJson({
        ...event.toJson(),
        'operation': 'reviseFocused',
      }).toJson()['operation'],
      'reviseFocused',
    );
    expect(
      () => AvaRealtimeServerEventV4.fromJson({
        ...event.toJson(),
        'unknown': true,
      }),
      throwsFormatException,
    );
    expect(
      () => AvaRealtimeServerEventV4.fromJson({
        ...event.toJson(),
        'operation': 'invalidOp',
      }),
      throwsFormatException,
    );
  });

  test('canvas draft fixture decodes with fixed non-authoritative trust', () {
    final fixture = _objectFixture('ava_realtime_canvas_draft_v4.json');
    final event = AvaRealtimeServerEventV4.fromJson(fixture);

    expect(event.type, AvaRealtimeServerEventTypeV4.canvasDraftAvailable);
    expect(event.isCanvas, isTrue);
    expect(event.toJson()['authoritative'], isFalse);
    expect(event.toJson()['interactive'], isFalse);
    expect(
      () => AvaRealtimeServerEventV4.fromJson({
        ...fixture,
        'authoritative': true,
      }),
      throwsFormatException,
    );
    expect(
      () => AvaRealtimeServerEventV4.fromJson({...fixture, 'sceneId': 'x'}),
      throwsFormatException,
    );
  });
}

Map<String, Object?> _objectFixture(String name) {
  final value = jsonDecode(File('protocol/fixtures/$name').readAsStringSync());
  return (value as Map).cast<String, Object?>();
}

List<Map<String, Object?>> _jsonLineFixtures(String name) {
  return [
    for (final line in File('protocol/fixtures/$name').readAsLinesSync())
      (jsonDecode(line) as Map).cast<String, Object?>(),
  ];
}
