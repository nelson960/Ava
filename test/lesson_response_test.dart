import 'package:ava/models/lesson_response.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('decodes and re-encodes a validated production lesson response', () {
    final response = LessonResponse.fromJson(const {
      'speech': 'One quarter is one of four equal parts.',
      'domain': 'math',
      'expression': 'encouraging',
      'gesture': 'pointAtDashboard',
      'diagram': {
        'kind': 'fractionCircle',
        'title': 'One quarter',
        'totalParts': 4,
        'highlightedParts': 1,
      },
    });

    expect(response.domain, LearningDomain.math);
    expect(response.gesture, AvaGesture.pointAtDashboard);
    expect(
      (response.diagram as FractionDiagramSpec).highlightedParts,
      1,
    );
    expect(response.toJson()['diagram'], isA<Map<String, Object?>>());
  });

  test('accepts a speech-only response without opening the dashboard', () {
    final response = LessonResponse.fromJson(const {
      'speech': 'The sky looks blue because blue light scatters strongly.',
      'domain': 'science',
      'expression': 'happy',
      'gesture': 'explain',
    });

    expect(response.diagram, isNull);
  });

  test('rejects unknown fields, enum values, and unsafe diagrams', () {
    expect(
      () => LessonResponse.fromJson(const {
        'speech': 'Hello.',
        'domain': 'math',
        'expression': 'encouraging',
        'gesture': 'nod',
        'untrustedHtml': '<script/>',
      }),
      throwsFormatException,
    );
    expect(
      () => LessonResponse.fromJson(const {
        'speech': 'Hello.',
        'domain': 'history',
        'expression': 'encouraging',
        'gesture': 'nod',
      }),
      throwsFormatException,
    );
    expect(
      () => LessonResponse.fromJson(const {
        'speech': 'Look at this.',
        'domain': 'math',
        'expression': 'encouraging',
        'gesture': 'pointAtDashboard',
        'diagram': {
          'kind': 'fractionCircle',
          'title': 'Invalid fraction',
          'totalParts': 4,
          'highlightedParts': 5,
        },
      }),
      throwsFormatException,
    );
  });
}
