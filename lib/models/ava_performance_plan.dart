import 'lesson_response.dart';

export 'diagram_spec.dart';

/// Semantic performance instructions returned by protocol v2.
///
/// These values are interpreted locally. They must never contain renderer,
/// skeleton, keyframe, duration, or raw SVG data.
enum AvaDelivery {
  neutral,
  warm,
  playful,
  thoughtful,
  encouraging,
  concerned,
}

enum AvaGazeIntent {
  child,
  thinkingAway,
  diagram,
  gestureTarget,
}

enum AvaExpressionIntent {
  neutral,
  warmSmile,
  curious,
  thinking,
  encouraging,
  surprised,
  concerned,
  serious,
}

enum AvaGestureIntent {
  none,
  acknowledge,
  explainSmall,
  explainWide,
  count,
  compare,
  presentLeft,
  presentRight,
  pointAtDashboard,
  reassure,
  celebrateSmall,
}

enum DiagramCueAction {
  show,
}

enum PerformanceEventType {
  turnAccepted,
  performancePlan,
  audioMetadata,
  audioChunk,
  characterTimingBatch,
  segmentBoundary,
  diagramCue,
  turnCompleted,
  turnCancelled,
  turnFailed,
}

enum PerformanceFailureCode {
  invalidPlan,
  modelRefusal,
  modelTimeout,
  modelUnavailable,
  speechTimeout,
  speechUnavailable,
  streamInterrupted,
  timingInvalid,
  memoryUnavailable,
  rendererUnavailable,
  cancelled,
  internalError,
}

class AvaDiagramCue {
  const AvaDiagramCue({
    required this.action,
    required this.diagram,
  });

  final DiagramCueAction action;
  final DiagramSpec diagram;

  factory AvaDiagramCue.fromJson(Map<String, Object?> json) {
    _requirePerformanceKeys(
      json,
      const {'action', 'diagram'},
      'diagram cue',
    );
    return AvaDiagramCue(
      action: _performanceEnumByName(
        DiagramCueAction.values,
        _requiredPerformanceString(json, 'action', 'diagram cue'),
        'diagram cue.action',
      ),
      diagram: DiagramSpec.fromJson(
        _requiredPerformanceObject(json['diagram'], 'diagram cue.diagram'),
      ),
    );
  }

  Map<String, Object?> toJson() => {
        'action': action.name,
        'diagram': diagram.toJson(),
      };
}

class AvaPerformanceSegment {
  const AvaPerformanceSegment({
    required this.id,
    required this.speech,
    required this.delivery,
    required this.expression,
    required this.gaze,
    required this.gesture,
    required this.diagramCue,
  });

  static final RegExp _validId = RegExp(r'^[a-z][a-z0-9_]{0,39}$');

  final String id;
  final String speech;
  final AvaDelivery delivery;
  final AvaExpressionIntent expression;
  final AvaGazeIntent gaze;
  final AvaGestureIntent gesture;
  final AvaDiagramCue? diagramCue;

  factory AvaPerformanceSegment.fromJson(Map<String, Object?> json) {
    _requirePerformanceKeys(
      json,
      const {
        'id',
        'speech',
        'delivery',
        'expression',
        'gaze',
        'gesture',
        'diagramCue',
      },
      'performance segment',
    );
    final rawDiagramCue = json['diagramCue'];
    final segment = AvaPerformanceSegment(
      id: _requiredPerformanceString(json, 'id', 'performance segment'),
      speech: _requiredPerformanceString(json, 'speech', 'performance segment'),
      delivery: _performanceEnumByName(
        AvaDelivery.values,
        _requiredPerformanceString(
          json,
          'delivery',
          'performance segment',
        ),
        'performance segment.delivery',
      ),
      expression: _performanceEnumByName(
        AvaExpressionIntent.values,
        _requiredPerformanceString(
          json,
          'expression',
          'performance segment',
        ),
        'performance segment.expression',
      ),
      gaze: _performanceEnumByName(
        AvaGazeIntent.values,
        _requiredPerformanceString(json, 'gaze', 'performance segment'),
        'performance segment.gaze',
      ),
      gesture: _performanceEnumByName(
        AvaGestureIntent.values,
        _requiredPerformanceString(json, 'gesture', 'performance segment'),
        'performance segment.gesture',
      ),
      diagramCue: rawDiagramCue == null
          ? null
          : AvaDiagramCue.fromJson(
              _requiredPerformanceObject(
                rawDiagramCue,
                'performance segment.diagramCue',
              ),
            ),
    );
    segment.validate();
    return segment;
  }

  Map<String, Object?> toJson() => {
        'id': id,
        'speech': speech,
        'delivery': delivery.name,
        'expression': expression.name,
        'gaze': gaze.name,
        'gesture': gesture.name,
        'diagramCue': diagramCue?.toJson(),
      };

  void validate() {
    if (!_validId.hasMatch(id)) {
      throw FormatException(
        'Performance segment id "$id" must be 1-40 lowercase ASCII '
        'letters, numbers, or underscores and begin with a letter.',
      );
    }
    final normalizedSpeech = speech.trim();
    if (normalizedSpeech.isEmpty) {
      throw const FormatException(
          'Performance segment speech cannot be empty.');
    }
    if (_codePointLength(normalizedSpeech) > 600) {
      throw const FormatException(
        'Performance segment speech exceeds the 600-character limit.',
      );
    }

    final hasDiagram = diagramCue != null;
    final pointsAtDiagram = gesture == AvaGestureIntent.pointAtDashboard;
    final looksAtDiagram = gaze == AvaGazeIntent.diagram;
    if (hasDiagram != pointsAtDiagram || hasDiagram != looksAtDiagram) {
      throw const FormatException(
        'diagramCue, gaze=diagram and gesture=pointAtDashboard must occur '
        'together.',
      );
    }

    if (gesture == AvaGestureIntent.celebrateSmall &&
        (delivery == AvaDelivery.concerned ||
            expression == AvaExpressionIntent.concerned ||
            expression == AvaExpressionIntent.serious)) {
      throw const FormatException(
        'gesture=celebrateSmall cannot use a concerned or serious tone.',
      );
    }
    if (delivery == AvaDelivery.playful &&
        (expression == AvaExpressionIntent.concerned ||
            expression == AvaExpressionIntent.serious)) {
      throw const FormatException(
        'delivery=playful cannot use a concerned or serious expression.',
      );
    }
  }
}

class AvaPerformancePlan {
  AvaPerformancePlan({required List<AvaPerformanceSegment> segments})
      : segments = List.unmodifiable(segments) {
    validate();
  }

  final List<AvaPerformanceSegment> segments;

  String get speech => segments.map((segment) => segment.speech).join(' ');

  factory AvaPerformancePlan.fromJson(Map<String, Object?> json) {
    _requirePerformanceKeys(json, const {'segments'}, 'performance plan');
    final rawSegments = json['segments'];
    if (rawSegments is! List) {
      throw const FormatException('performance plan.segments must be a list.');
    }
    return AvaPerformancePlan(
      segments: [
        for (var index = 0; index < rawSegments.length; index += 1)
          AvaPerformanceSegment.fromJson(
            _requiredPerformanceObject(
              rawSegments[index],
              'performance plan.segments[$index]',
            ),
          ),
      ],
    );
  }

  Map<String, Object?> toJson() => {
        'segments': [for (final segment in segments) segment.toJson()],
      };

  void validate() {
    if (segments.isEmpty || segments.length > 6) {
      throw FormatException(
        'A performance plan requires between 1 and 6 segments, got '
        '${segments.length}.',
      );
    }
    final ids = <String>{};
    var diagramCueCount = 0;
    for (final segment in segments) {
      segment.validate();
      if (!ids.add(segment.id)) {
        throw FormatException(
          'Performance segment id "${segment.id}" is duplicated.',
        );
      }
      if (segment.diagramCue != null) {
        diagramCueCount += 1;
      }
    }
    if (_codePointLength(speech) > 1800) {
      throw const FormatException(
        'Performance speech exceeds the 1800-character limit.',
      );
    }
    if (diagramCueCount > 1) {
      throw const FormatException(
        'A performance plan can contain at most one diagramCue.',
      );
    }
  }
}

class PerformanceTurnResponse {
  PerformanceTurnResponse({
    required this.protocolVersion,
    required this.performance,
  }) {
    if (protocolVersion != 2) {
      throw FormatException(
        'Ava performance protocolVersion must be exactly 2, got '
        '$protocolVersion.',
      );
    }
  }

  final int protocolVersion;
  final AvaPerformancePlan performance;

  factory PerformanceTurnResponse.fromJson(Map<String, Object?> json) {
    _requirePerformanceKeys(
      json,
      const {'protocolVersion', 'performance'},
      'performance turn response',
    );
    return PerformanceTurnResponse(
      protocolVersion: _requiredPerformanceInt(
        json,
        'protocolVersion',
        'performance turn response',
      ),
      performance: AvaPerformancePlan.fromJson(
        _requiredPerformanceObject(
          json['performance'],
          'performance turn response.performance',
        ),
      ),
    );
  }

  Map<String, Object?> toJson() => {
        'protocolVersion': protocolVersion,
        'performance': performance.toJson(),
      };
}

class PerformanceFailure {
  PerformanceFailure({
    required this.code,
    required this.message,
    required this.retryable,
  }) {
    if (message.trim().isEmpty) {
      throw const FormatException(
          'Performance failure message cannot be empty.');
    }
    if (_codePointLength(message.trim()) > 240) {
      throw const FormatException(
        'Performance failure message exceeds the 240-character limit.',
      );
    }
  }

  final PerformanceFailureCode code;
  final String message;
  final bool retryable;

  factory PerformanceFailure.fromJson(Map<String, Object?> json) {
    _requirePerformanceKeys(
      json,
      const {'code', 'message', 'retryable'},
      'performance failure',
    );
    final retryable = json['retryable'];
    if (retryable is! bool) {
      throw const FormatException(
        'performance failure.retryable must be a boolean.',
      );
    }
    return PerformanceFailure(
      code: _performanceEnumByName(
        PerformanceFailureCode.values,
        _requiredPerformanceString(json, 'code', 'performance failure'),
        'performance failure.code',
      ),
      message:
          _requiredPerformanceString(json, 'message', 'performance failure'),
      retryable: retryable,
    );
  }

  Map<String, Object?> toJson() => {
        'code': code.name,
        'message': message,
        'retryable': retryable,
      };
}

int _codePointLength(String value) => value.runes.length;

void _requirePerformanceKeys(
  Map<String, Object?> source,
  Set<String> allowed,
  String context,
) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final missing = allowed.difference(source.keys.toSet());
  if (missing.isNotEmpty) {
    throw FormatException(
      '$context is missing required fields: ${missing.join(', ')}.',
    );
  }
}

String _requiredPerformanceString(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _requiredPerformanceInt(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}

Map<String, Object?> _requiredPerformanceObject(
  Object? value,
  String context,
) {
  if (value is! Map) {
    throw FormatException('$context must be an object.');
  }
  final result = <String, Object?>{};
  for (final entry in value.entries) {
    if (entry.key is! String) {
      throw FormatException('$context contains a non-string field name.');
    }
    result[entry.key as String] = entry.value;
  }
  return result;
}

T _performanceEnumByName<T extends Enum>(
  List<T> values,
  String raw,
  String context,
) {
  final matches = values.where((value) => value.name == raw);
  if (matches.length != 1) {
    throw FormatException('$context has unsupported value "$raw".');
  }
  return matches.single;
}
