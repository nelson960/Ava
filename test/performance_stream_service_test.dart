import 'dart:convert';

import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/ava_performance_event.dart';
import 'package:ava/models/lesson_response.dart';
import 'package:ava/services/performance_stream_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('streams and validates one complete ordered performance turn', () async {
    final transport = _Transport(_response(_completeEvents()));
    final service = AvaPerformanceStreamService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/',
      ),
      transport: transport,
    );

    final events = await service.respond(_request()).toList();

    expect(events, hasLength(6));
    expect(events.first, isA<AvaTurnAcceptedEvent>());
    expect(events.last, isA<AvaTurnCompletedEvent>());
    expect(
      transport.endpoint,
      Uri.parse('https://ava.example/v2/performance-turns'),
    );
    expect(transport.headers?['x-ava-protocol-version'], '2');
    expect(
      jsonDecode(transport.body!)['protocolVersion'],
      2,
    );
  });

  test('rejects reordered events before they can drive Ava', () async {
    final events = _completeEvents();
    events[3]['sequence'] = 4;
    final service = AvaPerformanceStreamService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/',
      ),
      transport: _Transport(_response(events)),
    );

    await expectLater(
      service.respond(_request()).toList(),
      throwsA(
        isA<FormatException>().having(
          (error) => error.message,
          'message',
          contains('sequence mismatch'),
        ),
      ),
    );
  });

  test('requires an explicit terminal event', () async {
    final events = _completeEvents()..removeLast();
    final service = AvaPerformanceStreamService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/',
      ),
      transport: _Transport(_response(events)),
    );

    await expectLater(
      service.respond(_request()).toList(),
      throwsA(
        isA<FormatException>().having(
          (error) => error.message,
          'message',
          contains('without a terminal event'),
        ),
      ),
    );
  });

  test('surfaces non-success response with request identity', () async {
    final service = AvaPerformanceStreamService(
      configuration: AvaBackendConfiguration.parse(
        'https://ava.example/',
      ),
      transport: _Transport(
        AvaPerformanceBackendResponse(
          statusCode: 503,
          contentType: 'application/json',
          bytes: Stream.value(utf8.encode('{"error":"unavailable"}')),
          requestId: 'request-failed',
        ),
      ),
    );

    await expectLater(
      service.respond(_request()).toList(),
      throwsA(
        predicate(
          (error) =>
              error.toString().contains('HTTP 503') &&
              error.toString().contains('request-failed'),
        ),
      ),
    );
  });
}

ConversationRequest _request() => const ConversationRequest(
      childName: 'Mia',
      message: 'Hello',
      recentMessages: [],
    );

AvaPerformanceBackendResponse _response(
  List<Map<String, Object?>> events,
) =>
    AvaPerformanceBackendResponse(
      statusCode: 200,
      contentType: 'application/x-ndjson',
      bytes: Stream.value(
        utf8.encode('${events.map(jsonEncode).join('\n')}\n'),
      ),
      requestId: 'request-1234',
    );

List<Map<String, Object?>> _completeEvents() => [
      _event('turnAccepted', 0, {'requestId': 'request-1234'}),
      _event(
        'performancePlan',
        1,
        {
          'performance': {
            'segments': [
              {
                'id': 'reply',
                'speech': 'Hello there.',
                'delivery': 'warm',
                'expression': 'warmSmile',
                'gaze': 'child',
                'gesture': 'none',
                'diagramCue': null,
              },
            ],
          },
        },
      ),
      _event(
        'audioMetadata',
        2,
        {
          'audioFormat': 'pcm_s16le',
          'mediaType': 'audio/x-raw',
          'sampleRateHz': 24000,
          'channels': 1,
          'bitsPerSample': 16,
          'model': 'candidate-model',
          'voiceId': 'candidate-voice',
          'voiceLabel': 'Eve marry',
        },
      ),
      _event(
        'segmentBoundary',
        3,
        {'segmentId': 'reply', 'segmentIndex': 0},
      ),
      _event(
        'audioChunk',
        4,
        {
          'segmentId': 'reply',
          'chunkIndex': 0,
          'audioBase64': base64Encode([0, 0, 1, 0]),
        },
      ),
      _event('turnCompleted', 5, {'audioChunkCount': 1}),
    ];

Map<String, Object?> _event(
  String event,
  int sequence,
  Map<String, Object?> fields,
) =>
    {
      'protocolVersion': 2,
      'event': event,
      'sequence': sequence,
      'turnId': 'turn-1234',
      ...fields,
    };

class _Transport implements AvaPerformanceBackendTransport {
  _Transport(this.response);

  final AvaPerformanceBackendResponse response;
  Uri? endpoint;
  Map<String, String>? headers;
  String? body;

  @override
  Future<AvaPerformanceBackendResponse> open({
    required Uri endpoint,
    required Map<String, String> headers,
    required String body,
    required Duration timeout,
  }) async {
    this.endpoint = endpoint;
    this.headers = headers;
    this.body = body;
    return response;
  }

  @override
  void close() {}
}
