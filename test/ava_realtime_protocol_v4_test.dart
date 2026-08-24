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
    expect(translated['rendererCapabilityVersion'], 'ava-svg-renderer-v1');

    final playback = AvaRealtimeClientEventV4.playbackStarted(
      sessionId: 'session-1234',
      sequence: 1,
      turnId: 'turn-1234',
      streamId: 'stream-1234',
    ).toJson();
    expect(playback['protocolVersion'], 4);
    expect(playback['renderedSampleCount'], 0);
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

  test('canvas generation start is a strict session-scoped control', () {
    final event = AvaRealtimeServerEventV4.fromJson(
      _objectFixture('ava_realtime_canvas_generation_v4.json'),
    );

    expect(event.type, AvaRealtimeServerEventTypeV4.canvasGenerationStarted);
    expect(event.isCanvas, isTrue);
    expect(
      () => AvaRealtimeServerEventV4.fromJson({
        ...event.toJson(),
        'unknown': true,
      }),
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
