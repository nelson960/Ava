enum AvaRealtimeAgeBand {
  unknown,
  earlyChildhood,
  middleChildhood,
  preteen,
}

enum AvaRealtimeDelivery {
  neutral,
  warm,
  playful,
  thoughtful,
  encouraging,
  concerned,
}

/// Converts a locally stored date of birth into the only age information that
/// may cross Ava's provider boundary.
AvaRealtimeAgeBand deriveAvaRealtimeAgeBand(
  DateTime? dateOfBirth, {
  DateTime? at,
}) {
  if (dateOfBirth == null) return AvaRealtimeAgeBand.unknown;
  final reference = at ?? DateTime.now();
  final referenceDate =
      DateTime(reference.year, reference.month, reference.day);
  final birthDate = DateTime(
    dateOfBirth.year,
    dateOfBirth.month,
    dateOfBirth.day,
  );
  if (birthDate.isAfter(referenceDate)) return AvaRealtimeAgeBand.unknown;
  var age = referenceDate.year - birthDate.year;
  final birthdayThisYear = DateTime(
    referenceDate.year,
    birthDate.month,
    birthDate.day,
  );
  if (referenceDate.isBefore(birthdayThisYear)) age -= 1;
  if (age < 3 || age > 12) return AvaRealtimeAgeBand.unknown;
  if (age <= 5) return AvaRealtimeAgeBand.earlyChildhood;
  if (age <= 9) return AvaRealtimeAgeBand.middleChildhood;
  return AvaRealtimeAgeBand.preteen;
}

enum AvaRealtimeExpression {
  neutral,
  warmSmile,
  curious,
  thinking,
  encouraging,
  concerned,
  serious,
}

enum AvaRealtimeGaze {
  child,
  thinkingAway,
  canvas,
}

enum AvaRealtimeMotionIntent {
  none,
  acknowledge,
  presentCanvas,
  encourage,
}

enum AvaRealtimeTurnPhase {
  created,
  connecting,
  listening,
  planning,
  synthesizing,
  speaking,
  cancelling,
  completed,
  cancelled,
  failed,
}

final class AvaRealtimeTurnSegment {
  AvaRealtimeTurnSegment({
    required this.id,
    required this.speech,
    required this.delivery,
    required this.expression,
    required this.gaze,
    required this.motionIntent,
  }) {
    _validate();
  }

  final String id;
  final String speech;
  final AvaRealtimeDelivery delivery;
  final AvaRealtimeExpression expression;
  final AvaRealtimeGaze gaze;
  final AvaRealtimeMotionIntent motionIntent;

  factory AvaRealtimeTurnSegment.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'id',
        'speech',
        'delivery',
        'expression',
        'gaze',
        'motionIntent',
        'canvasCues',
      },
      'realtime segment',
    );
    final rawCues = json['canvasCues'];
    if (rawCues is! List || rawCues.isNotEmpty) {
      throw const FormatException(
        'Protocol-v4 speech segments cannot contain canvas cues.',
      );
    }
    return AvaRealtimeTurnSegment(
      id: _segmentId(json, 'id', 'realtime segment'),
      speech: _boundedString(json, 'speech', 'realtime segment', 600),
      delivery: _enumByName(
        AvaRealtimeDelivery.values,
        _requiredString(json, 'delivery', 'realtime segment'),
        'realtime segment.delivery',
      ),
      expression: _enumByName(
        AvaRealtimeExpression.values,
        _requiredString(json, 'expression', 'realtime segment'),
        'realtime segment.expression',
      ),
      gaze: _enumByName(
        AvaRealtimeGaze.values,
        _requiredString(json, 'gaze', 'realtime segment'),
        'realtime segment.gaze',
      ),
      motionIntent: _enumByName(
        AvaRealtimeMotionIntent.values,
        _requiredString(json, 'motionIntent', 'realtime segment'),
        'realtime segment.motionIntent',
      ),
    );
  }

  void _validate() {
    if (gaze == AvaRealtimeGaze.canvas) {
      throw const FormatException(
        'gaze=canvas requires canvasCues and vice versa.',
      );
    }
    if (motionIntent == AvaRealtimeMotionIntent.presentCanvas) {
      throw const FormatException(
        'motionIntent=presentCanvas requires canvasCues and vice versa.',
      );
    }
  }

  Map<String, Object?> toJson() => {
        'id': id,
        'speech': speech,
        'delivery': delivery.name,
        'expression': expression.name,
        'gaze': gaze.name,
        'motionIntent': motionIntent.name,
        'canvasCues': const <Object?>[],
      };
}

final class AvaRealtimeAlignmentSpan {
  const AvaRealtimeAlignmentSpan({
    required this.segmentId,
    required this.textStart,
    required this.textEnd,
    required this.sampleStart,
    required this.sampleEnd,
    required this.phoneme,
  });

  final String segmentId;
  final int textStart;
  final int textEnd;
  final int sampleStart;
  final int sampleEnd;
  final String? phoneme;

  factory AvaRealtimeAlignmentSpan.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'segmentId',
        'textStart',
        'textEnd',
        'sampleStart',
        'sampleEnd',
        'phoneme',
      },
      'alignment span',
    );
    final textStart = _requiredInt(json, 'textStart', 'alignment span');
    final textEnd = _requiredInt(json, 'textEnd', 'alignment span');
    final sampleStart = _requiredInt(json, 'sampleStart', 'alignment span');
    final sampleEnd = _requiredInt(json, 'sampleEnd', 'alignment span');
    if (textStart < 0 ||
        textEnd <= textStart ||
        textEnd > 1800 ||
        sampleStart < 0 ||
        sampleEnd <= sampleStart) {
      throw const FormatException('Alignment span ranges are invalid.');
    }
    final rawPhoneme = json['phoneme'];
    if (rawPhoneme != null &&
        (rawPhoneme is! String ||
            rawPhoneme.isEmpty ||
            rawPhoneme.runes.length > 16)) {
      throw const FormatException('alignment span.phoneme is invalid.');
    }
    return AvaRealtimeAlignmentSpan(
      segmentId: _segmentId(json, 'segmentId', 'alignment span'),
      textStart: textStart,
      textEnd: textEnd,
      sampleStart: sampleStart,
      sampleEnd: sampleEnd,
      phoneme: rawPhoneme as String?,
    );
  }

  Map<String, Object?> toJson() => {
        'segmentId': segmentId,
        'textStart': textStart,
        'textEnd': textEnd,
        'sampleStart': sampleStart,
        'sampleEnd': sampleEnd,
        'phoneme': phoneme,
      };
}

final class AvaRealtimeFailure {
  const AvaRealtimeFailure({
    required this.failureId,
    required this.component,
    required this.code,
    required this.message,
    required this.retryable,
  });

  final String failureId;
  final String component;
  final String code;
  final String message;
  final bool retryable;

  factory AvaRealtimeFailure.fromJson(Map<String, Object?> json) {
    _validateFailure(json);
    return AvaRealtimeFailure(
      failureId: json['failureId']! as String,
      component: json['component']! as String,
      code: json['code']! as String,
      message: json['message']! as String,
      retryable: json['retryable']! as bool,
    );
  }
}

void _validateFailure(Map<String, Object?> json) {
  _requireExactKeys(
    json,
    const {'failureId', 'component', 'code', 'message', 'retryable'},
    'realtime failure',
  );
  _protocolId(json, 'failureId', 'realtime failure');
  _allowedString(
    json,
    'component',
    'realtime failure',
    const {
      'realtimeProvider',
      'speechProvider',
      'canvasSpecialist',
      'memoryStore',
      'playback',
      'protocol',
      'coordinator',
    },
  );
  _allowedString(
    json,
    'code',
    'realtime failure',
    const {
      'authentication',
      'realtimeNegotiation',
      'modelInvalid',
      'modelTimeout',
      'modelUnavailable',
      'speechInvalid',
      'speechTimeout',
      'speechUnavailable',
      'protocolSequence',
      'canvasRejected',
      'memoryUnavailable',
      'playbackUnavailable',
      'cancelled',
      'internal',
      'turnContextFailed',
    },
  );
  _boundedString(json, 'message', 'realtime failure', 240);
  _requiredBool(json, 'retryable', 'realtime failure');
}

String _segmentId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  if (!RegExp(r'^[a-z][a-z0-9_]{0,39}$').hasMatch(value)) {
    throw FormatException('$context.$key has an invalid segment identifier.');
  }
  return value;
}

String _protocolId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(value)) {
    throw FormatException('$context.$key has an invalid identifier.');
  }
  return value;
}

String _allowedString(
  Map<String, Object?> json,
  String key,
  String context,
  Set<String> allowed,
) {
  final value = _requiredString(json, key, context);
  if (!allowed.contains(value)) {
    throw FormatException('$context.$key has unsupported value "$value".');
  }
  return value;
}

String _boundedString(
  Map<String, Object?> json,
  String key,
  String context,
  int maximum,
) {
  final value = _requiredString(json, key, context);
  if (value.runes.length > maximum) {
    throw FormatException('$context.$key exceeds the character limit.');
  }
  return value;
}

T _enumByName<T extends Enum>(
  List<T> values,
  String name,
  String context,
) {
  for (final value in values) {
    if (value.name == name) return value;
  }
  throw FormatException('$context has unsupported value "$name".');
}

void _requireExactKeys(
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

String _requiredString(
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

int _requiredInt(
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

bool _requiredBool(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! bool) {
    throw FormatException('$context.$key must be a boolean.');
  }
  return value;
}
