import 'dart:convert';
import 'dart:io';

import 'package:ava/models/ava_performance_plan.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('shared protocol v2 fixture round-trips strictly', () async {
    final fixture = await _fixture();

    final response = PerformanceTurnResponse.fromJson(fixture);

    expect(response.protocolVersion, 2);
    expect(response.performance.segments, hasLength(2));
    expect(
      response.performance.speech,
      'A quarter means one of four equal parts. '
      'Here, one of the four pieces is highlighted.',
    );
    final diagram = response.performance.segments.last.diagramCue?.diagram;
    expect((diagram as FractionDiagramSpec).totalParts, 4);
    expect(response.toJson(), fixture);
  });

  test('rejects raw motion fields and requires explicit nullable cues',
      () async {
    final rawMotion = await _fixture();
    final rawSegment = _segments(rawMotion).first;
    rawSegment['boneRotations'] = {
      'head': [0, 20, 0],
    };

    expect(
      () => PerformanceTurnResponse.fromJson(rawMotion),
      throwsFormatException,
    );

    final missingCue = await _fixture();
    _segments(missingCue).first.remove('diagramCue');

    expect(
      () => PerformanceTurnResponse.fromJson(missingCue),
      throwsFormatException,
    );
  });

  test('rejects incoherent diagram gaze and gesture combinations', () async {
    final wrongGesture = await _fixture();
    _segments(wrongGesture).last['gesture'] = 'explainSmall';
    expect(
      () => PerformanceTurnResponse.fromJson(wrongGesture),
      throwsFormatException,
    );

    final wrongGaze = await _fixture();
    _segments(wrongGaze).last['gaze'] = 'child';
    expect(
      () => PerformanceTurnResponse.fromJson(wrongGaze),
      throwsFormatException,
    );

    final cueMissing = await _fixture();
    _segments(cueMissing).last['diagramCue'] = null;
    expect(
      () => PerformanceTurnResponse.fromJson(cueMissing),
      throwsFormatException,
    );
  });

  test('bounds segment count, speech, ids and diagrams', () async {
    final duplicate = await _fixture();
    _segments(duplicate).last['id'] = 'intro';
    expect(
      () => PerformanceTurnResponse.fromJson(duplicate),
      throwsFormatException,
    );

    final base = <String, Object?>{
      'id': 'segment',
      'speech': List.filled(300, 'x').join(),
      'delivery': 'neutral',
      'expression': 'neutral',
      'gaze': 'child',
      'gesture': 'none',
      'diagramCue': null,
    };
    final tooMany = <String, Object?>{
      'segments': [
        for (var index = 0; index < 7; index += 1)
          {...base, 'id': 'segment_$index'},
      ],
    };
    expect(
      () => AvaPerformancePlan.fromJson(tooMany),
      throwsFormatException,
    );

    final tooLong = <String, Object?>{
      'segments': [
        for (var index = 0; index < 6; index += 1)
          {...base, 'id': 'segment_$index'},
      ],
    };
    expect(
      () => AvaPerformancePlan.fromJson(tooLong),
      throwsFormatException,
    );

    final multipleDiagrams = await _fixture();
    final duplicateDiagram = Map<String, Object?>.from(
      _segments(multipleDiagrams).last,
    )..['id'] = 'second_diagram';
    final performance =
        (multipleDiagrams['performance'] as Map).cast<String, Object?>();
    (performance['segments'] as List).add(duplicateDiagram);
    expect(
      () => PerformanceTurnResponse.fromJson(multipleDiagrams),
      throwsFormatException,
    );
  });

  test('rejects contradictory emotion and parses typed failures', () async {
    final contradiction = await _fixture();
    final segment = _segments(contradiction).first;
    segment['delivery'] = 'playful';
    segment['expression'] = 'serious';
    expect(
      () => PerformanceTurnResponse.fromJson(contradiction),
      throwsFormatException,
    );

    final failure = PerformanceFailure.fromJson(
      const {
        'code': 'speechUnavailable',
        'message': 'The speech provider is unavailable.',
        'retryable': true,
      },
    );
    expect(failure.code, PerformanceFailureCode.speechUnavailable);
    expect(
      () => PerformanceFailure.fromJson(
        const {
          'code': 'speechUnavailable',
          'message': 'Unavailable',
          'retryable': true,
          'fallbackSpeech': 'Use the device voice',
        },
      ),
      throwsFormatException,
    );
  });

  test('stream event vocabulary is mirrored exactly', () {
    expect(
      PerformanceEventType.values.map((event) => event.name),
      [
        'turnAccepted',
        'performancePlan',
        'audioMetadata',
        'audioChunk',
        'characterTimingBatch',
        'segmentBoundary',
        'diagramCue',
        'turnCompleted',
        'turnCancelled',
        'turnFailed',
      ],
    );
  });
}

Future<Map<String, Object?>> _fixture() async {
  final source = await File(
    'protocol/fixtures/ava_performance_plan_v2.json',
  ).readAsString();
  return (jsonDecode(source) as Map).cast<String, Object?>();
}

List<Map<String, Object?>> _segments(Map<String, Object?> fixture) {
  final performance = (fixture['performance'] as Map).cast<String, Object?>();
  return (performance['segments'] as List)
      .map((segment) => (segment as Map).cast<String, Object?>())
      .toList(growable: false);
}
