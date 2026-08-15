import 'dart:convert';

import 'package:ava/config/ava_backend_configuration.dart';
import 'package:ava/models/lesson_response.dart';
import 'package:ava/services/production_conversation_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('AvaBackendConfiguration', () {
    test('requires explicit configuration before model inference', () {
      final configuration = AvaBackendConfiguration.parse('');

      expect(configuration.isConfigured, isFalse);
      expect(() => configuration.lessonTurnsEndpoint, throwsStateError);
    });

    test('accepts HTTPS and normalizes the production lesson endpoint', () {
      final configuration =
          AvaBackendConfiguration.parse('https://ava.example/api');

      expect(
        configuration.realtimeSessionsEndpoint,
        Uri.parse('https://ava.example/api/v3/realtime/sessions'),
      );
      expect(
        configuration.lessonTurnsEndpoint,
        Uri.parse('https://ava.example/api/v1/lesson-turns'),
      );
      expect(
        configuration.resetConversationsEndpoint,
        Uri.parse('https://ava.example/api/v3/memory/conversations'),
      );
      expect(
        configuration.weeklyRecapEndpoint(DateTime(2026, 7, 27)),
        Uri.parse(
          'https://ava.example/api/v3/memory/weekly-recap?period_start=2026-07-27',
        ),
      );
    });

    test('rejects insecure remote URLs and credential-bearing URLs', () {
      expect(
        () => AvaBackendConfiguration.parse('http://ava.example'),
        throwsFormatException,
      );
      expect(
        () => AvaBackendConfiguration.parse('https://secret@ava.example'),
        throwsFormatException,
      );
      expect(
        AvaBackendConfiguration.parse(
          'http://127.0.0.1:8080',
          allowInsecureLocalhost: true,
        ).isConfigured,
        isTrue,
      );
    });
  });

  test('posts protocol v1 and decodes a strictly validated lesson', () async {
    final transport = _BackendTransportFake(
      const AvaBackendResponse(
        statusCode: 200,
        contentType: 'application/json',
        body: '''{
          "protocolVersion": 1,
          "lesson": {
            "speech": "One quarter is one of four equal parts.",
            "domain": "math",
            "expression": "encouraging",
            "gesture": "pointAtDashboard",
            "diagram": {
              "kind": "fractionCircle",
              "title": "One quarter",
              "totalParts": 4,
              "highlightedParts": 1
            }
          }
        }''',
        requestId: 'req-123',
      ),
    );
    final service = ProductionConversationService(
      configuration: AvaBackendConfiguration.parse('https://ava.example/api/'),
      transport: transport,
    );

    final response = await service.respond(
      const ConversationRequest(
        childName: 'Mia',
        message: 'What is one quarter?',
        recentMessages: [
          ConversationMessage(
            role: ConversationRole.ava,
            text: 'What would you like to learn?',
          ),
        ],
      ),
    );

    expect((response.diagram as FractionDiagramSpec).totalParts, 4);
    expect(transport.endpoint.path, '/api/v1/lesson-turns');
    expect(transport.headers['x-ava-protocol-version'], '1');
    final body = jsonDecode(transport.body) as Map<String, dynamic>;
    expect(body['protocolVersion'], 1);
    expect(
      (body['request'] as Map<String, dynamic>)['message'],
      'What is one quarter?',
    );
  });

  test('surfaces backend status without trusting an error response body', () {
    final service = ProductionConversationService(
      configuration: AvaBackendConfiguration.parse('https://ava.example/'),
      transport: _BackendTransportFake(
        const AvaBackendResponse(
          statusCode: 503,
          contentType: 'application/json',
          body: '{"unsafe":"details"}',
          requestId: 'req-down',
        ),
      ),
    );

    expect(
      () => service.respond(_request),
      throwsA(
        isA<AvaBackendException>()
            .having((error) => error.statusCode, 'status', 503)
            .having((error) => error.requestId, 'request', 'req-down'),
      ),
    );
  });

  test('rejects malformed or protocol-mismatched successful responses', () {
    Future<LessonResponse> respondWith(String body) {
      return ProductionConversationService(
        configuration: AvaBackendConfiguration.parse('https://ava.example/'),
        transport: _BackendTransportFake(
          AvaBackendResponse(
            statusCode: 200,
            contentType: 'application/json',
            body: body,
          ),
        ),
      ).respond(_request);
    }

    expect(() => respondWith('{'), throwsA(isA<AvaBackendException>()));
    expect(
      () => respondWith(
        '{"protocolVersion":2,"lesson":{"speech":"Hi",'
        '"domain":"general","expression":"neutral","gesture":"idle"}}',
      ),
      throwsFormatException,
    );
  });
}

const _request = ConversationRequest(
  childName: 'Mia',
  message: 'Teach me something.',
  recentMessages: [],
);

class _BackendTransportFake implements AvaBackendTransport {
  _BackendTransportFake(this.response);

  final AvaBackendResponse response;
  late Uri endpoint;
  late Map<String, String> headers;
  late String body;

  @override
  Future<AvaBackendResponse> postJson({
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
