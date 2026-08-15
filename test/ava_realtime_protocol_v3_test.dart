import 'dart:convert';
import 'dart:io';

import 'package:ava/models/ava_realtime_protocol.dart';
import 'package:ava/services/ava_realtime_session.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('session request contains only protocol version and derived age band',
      () {
    final requestJson = _objectFixture('ava_session_request_v3.json');
    final request = AvaSessionCreateRequestV3.fromJson(requestJson);
    expect(request.toJson(), requestJson);
    expect(
      request.ageBand,
      AvaRealtimeAgeBand.middleChildhood,
    );
    expect(
      deriveAvaRealtimeAgeBand(
        DateTime(2020, 7, 31),
        at: DateTime(2026, 7, 30),
      ),
      AvaRealtimeAgeBand.earlyChildhood,
    );
    expect(
      deriveAvaRealtimeAgeBand(
        DateTime(2017, 7, 30),
        at: DateTime(2026, 7, 30),
      ),
      AvaRealtimeAgeBand.middleChildhood,
    );
    expect(
      deriveAvaRealtimeAgeBand(
        DateTime(2014, 7, 30),
        at: DateTime(2026, 7, 30),
      ),
      AvaRealtimeAgeBand.preteen,
    );
    expect(
      deriveAvaRealtimeAgeBand(
        DateTime(2027, 1, 1),
        at: DateTime(2026, 7, 30),
      ),
      AvaRealtimeAgeBand.unknown,
    );
  });

  test('shared protocol-v3 plan and bootstrap fixtures round-trip', () {
    final conversationJson =
        _objectFixture('ava_turn_plan_conversation_v3.json');
    final conversation = AvaRealtimeTurnPlan.fromJson(conversationJson);
    expect(conversation.toJson(), conversationJson);
    expect(conversation.segments, hasLength(1));
    expect(conversation.segments.single.canvasCues, isEmpty);

    final planJson = _objectFixture('ava_turn_plan_v3.json');
    final plan = AvaRealtimeTurnPlan.fromJson(planJson);
    expect(plan.toJson(), planJson);
    expect(plan.segments.map((segment) => segment.id), ['answer', 'visual']);
    expect(plan.segments[1].canvasCues.single.transaction.revision, 1);

    final bootstrapJson = _objectFixture('ava_session_bootstrap_v3.json');
    final bootstrap = AvaSessionBootstrapV3.fromJson(bootstrapJson);
    expect(bootstrap.toJson(), bootstrapJson);
    expect(bootstrap.sessionId, 'session-1234');
    expect(Uri.parse(bootstrap.webrtcUrl).scheme, 'https');
  });

  test('session bootstrap rejects credential-bearing and non-UTC URLs', () {
    final credentials = _objectFixture('ava_session_bootstrap_v3.json')
      ..['eventUrl'] =
          'wss://secret@ava.example/v3/realtime/sessions/session-1234/events';
    expect(
      () => AvaSessionBootstrapV3.fromJson(credentials),
      throwsFormatException,
    );

    final insecureMedia = _objectFixture('ava_session_bootstrap_v3.json')
      ..['webrtcUrl'] =
          'http://ava.example/v3/realtime/sessions/session-1234/webrtc';
    expect(
      () => AvaSessionBootstrapV3.fromJson(insecureMedia),
      throwsFormatException,
    );

    final localExpiry = _objectFixture('ava_session_bootstrap_v3.json')
      ..['expiresAt'] = '2026-07-30T12:00:00';
    expect(
      () => AvaSessionBootstrapV3.fromJson(localExpiry),
      throwsFormatException,
    );
  });

  test('insecure bootstrap channels require explicit loopback permission', () {
    final local = _objectFixture('ava_session_bootstrap_v3.json')
      ..['eventUrl'] =
          'ws://127.0.0.1:8080/v3/realtime/sessions/session-1234/events'
      ..['webrtcUrl'] =
          'http://127.0.0.1:8080/v3/realtime/sessions/session-1234/webrtc';

    expect(
      () => AvaSessionBootstrapV3.fromJson(local),
      throwsFormatException,
    );
    final accepted = AvaSessionBootstrapV3.fromJson(
      local,
      allowInsecureLoopback: true,
    );
    expect(Uri.parse(accepted.eventUrl).scheme, 'ws');

    local['eventUrl'] = 'ws://ava.example.test/v3/realtime/events';
    expect(
      () => AvaSessionBootstrapV3.fromJson(
        local,
        allowInsecureLoopback: true,
      ),
      throwsFormatException,
    );
  });

  test('shared event fixture round-trips and reaches completed state', () {
    final payloads = _eventFixtures();
    final events = [
      for (final payload in payloads) AvaRealtimeServerEvent.fromJson(payload),
    ];
    expect(
      [for (final event in events) event.toJson()],
      payloads,
    );

    final machine = AvaRealtimeTurnStateMachine();
    for (final event in events) {
      machine.accept(event);
    }
    expect(machine.snapshot.phase, AvaRealtimeTurnPhase.completed);
    expect(machine.snapshot.terminal, isTrue);
    expect(machine.snapshot.segmentCount, 2);
    expect(machine.snapshot.receivedSampleCount, 8);
    final alignment = events.firstWhere(
      (event) => event.type == AvaRealtimeServerEventType.alignmentBatch,
    );
    expect(alignment.alignmentSpans.single.phoneme, 'AH');
    final chunk = events.firstWhere(
      (event) => event.type == AvaRealtimeServerEventType.audioChunk,
    );
    expect(chunk.byteLength, chunk.sampleCount! * 2);
    expect(events.last.deliveredText, startsWith('A plant cell'));
  });

  test('optional canvas rejection is non-terminal and speech can complete', () {
    final payloads = _eventFixtures();
    final insertAt = payloads.indexWhere(
          (payload) => payload['event'] == 'canvasCue',
        ) +
        1;
    final prior = payloads[insertAt - 1];
    for (var index = insertAt; index < payloads.length; index += 1) {
      payloads[index]['sequence'] = (payloads[index]['sequence']! as int) + 1;
    }
    payloads.insert(insertAt, {
      'protocolVersion': 3,
      'event': 'canvasRejected',
      'sessionId': prior['sessionId'],
      'turnId': prior['turnId'],
      'sequence': (prior['sequence']! as int) + 1,
      'revision': prior['revision'],
      'failure': {
        'failureId': 'failure-protocol-canvas',
        'component': 'canvasSpecialist',
        'code': 'canvasRejected',
        'message': 'The optional visual could not be safely prepared.',
        'retryable': false,
      },
    });

    final machine = AvaRealtimeTurnStateMachine();
    final events = [
      for (final payload in payloads) AvaRealtimeServerEvent.fromJson(payload),
    ];
    for (final event in events) {
      machine.accept(event);
    }

    final rejection = events.firstWhere(
      (event) => event.type == AvaRealtimeServerEventType.canvasRejected,
    );
    expect(rejection.failure?.code, 'canvasRejected');
    expect(machine.snapshot.phase, AvaRealtimeTurnPhase.completed);
  });

  test('stale canvas interaction rejection is session-scoped', () {
    final machine = AvaRealtimeTurnStateMachine();
    for (final payload in _eventFixtures()) {
      machine.accept(AvaRealtimeServerEvent.fromJson(payload));
    }
    final terminal = machine.snapshot;
    final rejection = AvaRealtimeServerEvent.fromJson({
      'protocolVersion': 3,
      'event': 'interactionRejected',
      'sessionId': 'session-1234',
      'turnId': 'turn-1234',
      'sequence': terminal.nextSequence,
      'revision': terminal.revision,
      'sceneId': 'plant_cell_scene',
      'sceneRevision': 1,
      'interactionRevision': 7,
      'bindingId': 'select_nucleus',
      'rejectionCode': 'interactionRevisionStale',
      'message': 'Canvas interaction revision is stale.',
    });

    final snapshot = machine.accept(rejection);
    expect(rejection.interactionRejectionCode,
        AvaCanvasInteractionRejectionCode.interactionRevisionStale);
    expect(snapshot.terminal, isTrue);
    expect(snapshot.phase, AvaRealtimeTurnPhase.completed);
    expect(snapshot.nextSequence, terminal.nextSequence + 1);
  });

  test(
    'invalid nested canvas becomes a same-sequence optional rejection',
    () {
      final cue = _eventFixtures().firstWhere(
        (payload) => payload['event'] == 'canvasCue',
      );
      final transaction = (cue['transaction']! as Map).cast<String, Object?>();
      final operations = transaction['operations']! as List;
      final create = (operations.firstWhere(
        (operation) =>
            (operation as Map<String, Object?>)['operation'] == 'create',
      ) as Map)
          .cast<String, Object?>();
      final node = (create['node']! as Map).cast<String, Object?>();
      node.remove('parentId');

      late AvaRealtimeCanvasPayloadException rejection;
      try {
        AvaRealtimeServerEvent.fromJson(cue);
        fail('Invalid nested canvas payload was accepted.');
      } on AvaRealtimeCanvasPayloadException catch (error) {
        rejection = error;
      }

      final event = rejection.rejectionEvent;
      expect(event.type, AvaRealtimeServerEventType.canvasRejected);
      expect(event.sessionId, cue['sessionId']);
      expect(event.turnId, cue['turnId']);
      expect(event.sequence, cue['sequence']);
      expect(event.revision, cue['revision']);
      expect(event.failure?.code, 'canvasRejected');
      expect(rejection.detail, contains('parentId'));

      final payloads = _eventFixtures();
      final cueIndex = payloads.indexWhere(
        (payload) => payload['event'] == 'canvasCue',
      );
      final machine = AvaRealtimeTurnStateMachine();
      for (final payload in payloads.take(cueIndex)) {
        machine.accept(AvaRealtimeServerEvent.fromJson(payload));
      }
      machine.accept(event);
      for (final payload in payloads.skip(cueIndex + 1)) {
        machine.accept(AvaRealtimeServerEvent.fromJson(payload));
      }
      expect(machine.snapshot.phase, AvaRealtimeTurnPhase.completed);
      expect(machine.snapshot.terminal, isTrue);
    },
  );

  test('shared client control events round-trip strictly', () {
    final payloads = _listFixture('ava_realtime_client_events_v3.json');
    final events = [
      for (final payload in payloads) AvaRealtimeClientEvent.fromJson(payload),
    ];
    expect([for (final event in events) event.toJson()], payloads);
    expect(events.first.type, AvaRealtimeClientEventType.clientReady);
    final canvas = events.firstWhere(
      (event) => event.type == AvaRealtimeClientEventType.canvasInteraction,
    );
    expect(canvas.turnId, isNull);
    expect(events.last.requestId, 'cancel-1234');
  });

  test('plan rejects raw SVG, unsafe paths and missing anchor phrases', () {
    final rawSvg = _objectFixture('ava_turn_plan_v3.json');
    final cue = ((rawSvg['segments'] as List)[1]
        as Map<String, Object?>)['canvasCues'] as List;
    (cue.single as Map<String, Object?>)['rawSvg'] = '<svg onload="bad()"/>';
    expect(
      () => AvaRealtimeTurnPlan.fromJson(rawSvg),
      throwsFormatException,
    );

    final unsafePath = _objectFixture('ava_turn_plan_v3.json');
    final transaction = _firstTransaction(unsafePath);
    (transaction['operations'] as List).add({
      'operation': 'create',
      'node': {
        'id': 'unsafe_path',
        'parentId': null,
        'label': 'Unsafe path',
        'visible': true,
        'transform': {
          'translateX': 0.0,
          'translateY': 0.0,
          'scale': 1.0,
          'rotationDegrees': 0.0,
        },
        'style': {
          'fill': 'transparent',
          'stroke': 'ink',
          'strokeWidth': 2.0,
          'opacity': 1.0,
          'strokeCap': 'round',
        },
        'kind': 'path',
        'pathData': 'M 0 0 <script>',
      },
    });
    expect(
      () => AvaRealtimeTurnPlan.fromJson(unsafePath),
      throwsFormatException,
    );

    final badAnchor = _objectFixture('ava_turn_plan_v3.json');
    final badCue = (((badAnchor['segments'] as List)[1]
            as Map<String, Object?>)['canvasCues'] as List)
        .single as Map<String, Object?>;
    (badCue['anchor'] as Map<String, Object?>)['phrase'] = 'not in speech';
    expect(
      () => AvaRealtimeTurnPlan.fromJson(badAnchor),
      throwsFormatException,
    );
  });

  test('plan rejects unknown enums and oversized speech', () {
    final unknownEnum = _objectFixture('ava_turn_plan_v3.json');
    ((unknownEnum['segments'] as List).first
        as Map<String, Object?>)['delivery'] = 'dramatic';
    expect(
      () => AvaRealtimeTurnPlan.fromJson(unknownEnum),
      throwsFormatException,
    );

    final oversized = _objectFixture('ava_turn_plan_v3.json');
    ((oversized['segments'] as List).first as Map<String, Object?>)['speech'] =
        List.filled(601, 'a').join();
    expect(
      () => AvaRealtimeTurnPlan.fromJson(oversized),
      throwsFormatException,
    );
  });

  test('speech segment limit matches plan and server event', () {
    final plan = _objectFixture('ava_turn_plan_conversation_v3.json');
    plan['segments'] = _speechSegments(avaRealtimeMaxSpeechSegments);
    expect(
      AvaRealtimeTurnPlan.fromJson(plan).segments,
      hasLength(avaRealtimeMaxSpeechSegments),
    );

    final oversized = _objectFixture('ava_turn_plan_conversation_v3.json');
    oversized['segments'] = _speechSegments(
      avaRealtimeMaxSpeechSegments + 1,
    );
    expect(
      () => AvaRealtimeTurnPlan.fromJson(oversized),
      throwsFormatException,
    );

    final segmentReady = _eventFixtures()
        .firstWhere((payload) => payload['event'] == 'segmentReady');
    segmentReady['segmentIndex'] = avaRealtimeMaxSpeechSegments - 1;
    expect(
      AvaRealtimeServerEvent.fromJson(segmentReady).segmentIndex,
      avaRealtimeMaxSpeechSegments - 1,
    );

    segmentReady['segmentIndex'] = avaRealtimeMaxSpeechSegments;
    expect(
      () => AvaRealtimeServerEvent.fromJson(segmentReady),
      throwsFormatException,
    );
  });

  test('audio metadata and binary descriptors fail loudly', () {
    final metadata = _eventFixtures()
        .firstWhere((payload) => payload['event'] == 'audioMetadata');
    metadata['sampleRateHz'] = 44100;
    expect(
      () => AvaRealtimeServerEvent.fromJson(metadata),
      throwsFormatException,
    );

    final descriptor = _eventFixtures()
        .firstWhere((payload) => payload['event'] == 'audioChunk');
    descriptor['byteLength'] = 7;
    expect(
      () => AvaRealtimeServerEvent.fromJson(descriptor),
      throwsFormatException,
    );
  });

  test('state machine rejects duplicate sequence and events after terminal',
      () {
    final events = [
      for (final payload in _eventFixtures())
        AvaRealtimeServerEvent.fromJson(payload),
    ];
    final duplicate = AvaRealtimeTurnStateMachine();
    duplicate.accept(events.first);
    expect(
      () => duplicate.accept(events.first),
      throwsA(isA<AvaRealtimeStateException>()),
    );

    final completed = AvaRealtimeTurnStateMachine();
    for (final event in events) {
      completed.accept(event);
    }
    expect(
      () => completed.accept(events.last),
      throwsA(isA<AvaRealtimeStateException>()),
    );
  });

  test('speaking is a hard end-of-synthesis boundary for PCM', () {
    final machine = AvaRealtimeTurnStateMachine();
    for (final payload in _eventFixtures().take(13)) {
      machine.accept(AvaRealtimeServerEvent.fromJson(payload));
    }
    expect(machine.snapshot.phase, AvaRealtimeTurnPhase.speaking);
    expect(
      () => machine.accept(
        AvaRealtimeServerEvent.fromJson({
          'protocolVersion': 3,
          'event': 'audioChunk',
          'sessionId': 'session-1234',
          'turnId': 'turn-1234',
          'sequence': 13,
          'revision': 5,
          'streamId': 'stream-1234',
          'chunkIndex': 2,
          'sampleOffset': 8,
          'sampleCount': 4,
          'byteLength': 8,
        }),
      ),
      throwsA(isA<AvaRealtimeStateException>()),
    );
  });

  test('canvas cue cannot target audio that has not arrived', () {
    final payloads = _eventFixtures();
    final cue = Map<String, Object?>.from(payloads[10])..['sampleOffset'] = 5;
    final machine = AvaRealtimeTurnStateMachine();
    for (final payload in payloads.take(10)) {
      machine.accept(AvaRealtimeServerEvent.fromJson(payload));
    }
    expect(
      () => machine.accept(AvaRealtimeServerEvent.fromJson(cue)),
      throwsA(isA<AvaRealtimeStateException>()),
    );
  });

  test('state machine accepts cancellation from every active stage', () {
    for (final scenario in const [
      (1, 0),
      (2, 0),
      (4, 0),
      (8, 0),
      (12, 4),
    ]) {
      final payloads = _eventFixtures().take(scenario.$1).toList();
      final current = payloads.last;
      final sequence = (current['sequence']! as int) + 1;
      final revision = (current['revision']! as int) + 1;
      payloads.add({
        'protocolVersion': 3,
        'event': 'turnPhaseChanged',
        'sessionId': 'session-1234',
        'turnId': 'turn-1234',
        'sequence': sequence,
        'revision': revision,
        'phase': 'cancelling',
      });
      payloads.add({
        'protocolVersion': 3,
        'event': 'turnCancelled',
        'sessionId': 'session-1234',
        'turnId': 'turn-1234',
        'sequence': sequence + 1,
        'revision': revision + 1,
        'deliveredSampleCount': scenario.$2,
        'deliveredText': scenario.$2 == 0 ? '' : 'A plant',
        'reason': 'childStartedSpeaking',
      });

      final machine = AvaRealtimeTurnStateMachine();
      for (final payload in payloads) {
        machine.accept(AvaRealtimeServerEvent.fromJson(payload));
      }
      expect(machine.snapshot.phase, AvaRealtimeTurnPhase.cancelled);
      expect(machine.snapshot.terminal, isTrue);
    }
  });

  test('cancel registration is idempotent by request id', () {
    final machine = AvaRealtimeTurnStateMachine();
    machine.accept(
      AvaRealtimeServerEvent.fromJson(_eventFixtures().first),
    );
    expect(machine.registerCancellation('cancel-1234'), isTrue);
    expect(machine.registerCancellation('cancel-1234'), isFalse);
    expect(
      () => machine.registerCancellation('cancel-5678'),
      throwsA(isA<AvaRealtimeStateException>()),
    );
  });

  test('state machine completes speech when persistence needs retry', () {
    final payloads = _eventFixtures().take(13).toList()
      ..add({
        'protocolVersion': 3,
        'event': 'memoryPersistenceFailed',
        'sessionId': 'session-1234',
        'turnId': 'turn-1234',
        'sequence': 13,
        'revision': 5,
        'failure': {
          'failureId': 'failure-protocol-memory',
          'component': 'memoryStore',
          'code': 'memoryUnavailable',
          'message': 'Delivered turn could not be persisted.',
          'retryable': true,
        },
      })
      ..add({
        'protocolVersion': 3,
        'event': 'turnCompleted',
        'sessionId': 'session-1234',
        'turnId': 'turn-1234',
        'sequence': 14,
        'revision': 6,
        'deliveredSampleCount': 8,
        'deliveredText': 'A plant',
      });

    final machine = AvaRealtimeTurnStateMachine();
    for (final payload in payloads) {
      machine.accept(AvaRealtimeServerEvent.fromJson(payload));
    }
    expect(machine.snapshot.phase, AvaRealtimeTurnPhase.completed);
    expect(machine.snapshot.receivedSampleCount, 8);
  });

  test('memory refresh warning is non-terminal while listening', () {
    final payloads = _eventFixtures().take(3).toList()
      ..add({
        'protocolVersion': 3,
        'event': 'memoryPersistenceFailed',
        'sessionId': 'session-1234',
        'turnId': 'turn-1234',
        'sequence': 3,
        'revision': 2,
        'failure': {
          'failureId': 'failure-protocol-memory-refresh',
          'component': 'memoryStore',
          'code': 'memoryUnavailable',
          'message': 'Conversation memory context could not be refreshed.',
          'retryable': true,
        },
      });

    final machine = AvaRealtimeTurnStateMachine();
    for (final payload in payloads) {
      machine.accept(AvaRealtimeServerEvent.fromJson(payload));
    }

    expect(machine.snapshot.phase, AvaRealtimeTurnPhase.listening);
    expect(machine.snapshot.terminal, isFalse);
  });

  test('bounded buffer fails loudly and deterministic transport is ordered',
      () async {
    final buffer = BoundedRealtimeBuffer<List<int>>(
      maxItems: 2,
      maxWeight: 4,
      weigh: (value) => value.length,
    );
    buffer.add([1, 2]);
    buffer.add([3, 4]);
    expect(
      () => buffer.add([5]),
      throwsA(isA<AvaRealtimeBufferOverflow>()),
    );
    expect(buffer.removeFirst(), [1, 2]);
    buffer.close();
    expect(buffer.removeFirst(), [3, 4]);
    expect(
      buffer.removeFirst,
      throwsA(isA<AvaRealtimeBufferClosed>()),
    );

    final events = [
      for (final payload in _eventFixtures().take(3))
        AvaRealtimeServerEvent.fromJson(payload),
    ];
    final frames = [
      for (final event in events) AvaRealtimeControlFrame(event),
    ];
    final transport = DeterministicAvaRealtimeTransport(frames);
    expect(await transport.frames().toList(), frames);
    final ready = AvaRealtimeClientEvent.fromJson(
      _listFixture('ava_realtime_client_events_v3.json').first,
    );
    await transport.send(ready);
    expect(transport.sentEvents, [ready]);
    await transport.close();
    expect(transport.isClosed, isTrue);
  });
}

Map<String, Object?> _objectFixture(String name) {
  final decoded = jsonDecode(
    File('protocol/fixtures/$name').readAsStringSync(),
  );
  return (decoded as Map).cast<String, Object?>();
}

List<Map<String, Object?>> _eventFixtures() {
  return _listFixture('ava_realtime_events_v3.json');
}

List<Map<String, Object?>> _listFixture(String name) {
  final decoded =
      jsonDecode(File('protocol/fixtures/$name').readAsStringSync());
  return [
    for (final event in decoded as List) (event as Map).cast<String, Object?>(),
  ];
}

List<Map<String, Object?>> _speechSegments(int count) {
  final plan = _objectFixture('ava_turn_plan_conversation_v3.json');
  final template = (plan['segments'] as List).single;
  return [
    for (var index = 0; index < count; index += 1)
      {
        ...(jsonDecode(jsonEncode(template)) as Map).cast<String, Object?>(),
        'id': 'part_${index + 1}',
        'speech': 'Part ${index + 1}.',
      },
  ];
}

Map<String, Object?> _firstTransaction(Map<String, Object?> plan) {
  final segment = (plan['segments'] as List)[1] as Map<String, Object?>;
  final cue = (segment['canvasCues'] as List).single as Map<String, Object?>;
  return cue['transaction'] as Map<String, Object?>;
}
