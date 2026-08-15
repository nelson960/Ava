import 'dart:convert';
import 'dart:typed_data';

import 'ava_performance_plan.dart';

sealed class AvaPerformanceEvent {
  const AvaPerformanceEvent({
    required this.event,
    required this.sequence,
    required this.turnId,
  });

  static final RegExp _validTurnId = RegExp(r'^[A-Za-z0-9._:-]{8,64}$');
  static const _commonKeys = {
    'protocolVersion',
    'event',
    'sequence',
    'turnId',
  };

  final PerformanceEventType event;
  final int sequence;
  final String turnId;

  bool get isTerminal =>
      event == PerformanceEventType.turnCompleted ||
      event == PerformanceEventType.turnCancelled ||
      event == PerformanceEventType.turnFailed;

  factory AvaPerformanceEvent.fromJson(Map<String, Object?> json) {
    final protocolVersion = _requiredInt(json, 'protocolVersion', 'event');
    if (protocolVersion != 2) {
      throw FormatException(
        'Performance event protocolVersion must be exactly 2, got '
        '$protocolVersion.',
      );
    }
    final event = _enumByName(
      PerformanceEventType.values,
      _requiredString(json, 'event', 'event'),
      'event.event',
    );
    final sequence = _requiredInt(json, 'sequence', 'event');
    if (sequence < 0) {
      throw const FormatException(
        'Performance event sequence cannot be negative.',
      );
    }
    final turnId = _requiredString(json, 'turnId', 'event');
    if (!_validTurnId.hasMatch(turnId)) {
      throw const FormatException(
        'Performance event turnId has an invalid format.',
      );
    }

    return switch (event) {
      PerformanceEventType.turnAccepted => AvaTurnAcceptedEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.performancePlan => AvaPerformancePlanEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.audioMetadata => AvaAudioMetadataEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.audioChunk => AvaAudioChunkEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.characterTimingBatch =>
        AvaCharacterTimingBatchEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.segmentBoundary => AvaSegmentBoundaryEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.diagramCue => AvaDiagramCueEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.turnCompleted => AvaTurnCompletedEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.turnCancelled => AvaTurnCancelledEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
      PerformanceEventType.turnFailed => AvaTurnFailedEvent._fromJson(
          json,
          sequence: sequence,
          turnId: turnId,
        ),
    };
  }

  static void requireKeys(
    Map<String, Object?> json,
    Set<String> specific,
    String context,
  ) {
    _requireExactKeys(json, {..._commonKeys, ...specific}, context);
  }
}

final class AvaTurnAcceptedEvent extends AvaPerformanceEvent {
  const AvaTurnAcceptedEvent._({
    required super.sequence,
    required super.turnId,
    required this.requestId,
  }) : super(event: PerformanceEventType.turnAccepted);

  final String requestId;

  factory AvaTurnAcceptedEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(json, const {'requestId'}, 'turnAccepted');
    return AvaTurnAcceptedEvent._(
      sequence: sequence,
      turnId: turnId,
      requestId: _requiredString(json, 'requestId', 'turnAccepted'),
    );
  }
}

final class AvaPerformancePlanEvent extends AvaPerformanceEvent {
  const AvaPerformancePlanEvent._({
    required super.sequence,
    required super.turnId,
    required this.performance,
  }) : super(event: PerformanceEventType.performancePlan);

  final AvaPerformancePlan performance;

  factory AvaPerformancePlanEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {'performance'},
      'performancePlan',
    );
    return AvaPerformancePlanEvent._(
      sequence: sequence,
      turnId: turnId,
      performance: AvaPerformancePlan.fromJson(
        _requiredObject(json['performance'], 'performancePlan.performance'),
      ),
    );
  }
}

final class AvaAudioMetadataEvent extends AvaPerformanceEvent {
  const AvaAudioMetadataEvent._({
    required super.sequence,
    required super.turnId,
    required this.sampleRateHz,
    required this.model,
    required this.voiceId,
    required this.voiceLabel,
  }) : super(event: PerformanceEventType.audioMetadata);

  final int sampleRateHz;
  final String model;
  final String voiceId;
  final String voiceLabel;

  factory AvaAudioMetadataEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {
        'audioFormat',
        'mediaType',
        'sampleRateHz',
        'channels',
        'bitsPerSample',
        'model',
        'voiceId',
        'voiceLabel',
      },
      'audioMetadata',
    );
    if (_requiredString(json, 'audioFormat', 'audioMetadata') != 'pcm_s16le' ||
        _requiredString(json, 'mediaType', 'audioMetadata') != 'audio/x-raw' ||
        _requiredInt(json, 'channels', 'audioMetadata') != 1 ||
        _requiredInt(json, 'bitsPerSample', 'audioMetadata') != 16) {
      throw const FormatException(
        'Ava supports only mono signed 16-bit little-endian PCM audio.',
      );
    }
    final sampleRate = _requiredInt(json, 'sampleRateHz', 'audioMetadata');
    if (sampleRate != 24000) {
      throw FormatException(
        'Ava expected 24000 Hz speech audio, got $sampleRate Hz.',
      );
    }
    return AvaAudioMetadataEvent._(
      sequence: sequence,
      turnId: turnId,
      sampleRateHz: sampleRate,
      model: _requiredString(json, 'model', 'audioMetadata'),
      voiceId: _requiredString(json, 'voiceId', 'audioMetadata'),
      voiceLabel: _requiredString(json, 'voiceLabel', 'audioMetadata'),
    );
  }
}

final class AvaAudioChunkEvent extends AvaPerformanceEvent {
  AvaAudioChunkEvent._({
    required super.sequence,
    required super.turnId,
    required this.segmentId,
    required this.chunkIndex,
    required Uint8List audio,
  })  : audio = Uint8List.fromList(audio),
        super(event: PerformanceEventType.audioChunk);

  static const _maximumAudioChunkBytes = 2 * 1024 * 1024;

  final String segmentId;
  final int chunkIndex;
  final Uint8List audio;

  factory AvaAudioChunkEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {'segmentId', 'chunkIndex', 'audioBase64'},
      'audioChunk',
    );
    final chunkIndex = _requiredInt(json, 'chunkIndex', 'audioChunk');
    if (chunkIndex < 0) {
      throw const FormatException('audioChunk.chunkIndex cannot be negative.');
    }
    final Uint8List audio;
    try {
      audio = base64Decode(
        _requiredString(json, 'audioBase64', 'audioChunk'),
      );
    } on FormatException {
      throw const FormatException('audioChunk.audioBase64 is invalid.');
    }
    if (audio.isEmpty ||
        audio.length > _maximumAudioChunkBytes ||
        audio.length.isOdd) {
      throw const FormatException(
        'audioChunk must contain bounded complete PCM16 samples.',
      );
    }
    return AvaAudioChunkEvent._(
      sequence: sequence,
      turnId: turnId,
      segmentId: _requiredSegmentId(json, 'segmentId', 'audioChunk'),
      chunkIndex: chunkIndex,
      audio: audio,
    );
  }
}

final class AvaCharacterTiming {
  const AvaCharacterTiming({
    required this.character,
    required this.start,
    required this.end,
  });

  final String character;
  final Duration start;
  final Duration end;

  factory AvaCharacterTiming.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'character', 'startSeconds', 'endSeconds'},
      'characterTiming',
    );
    final character = _requiredString(json, 'character', 'characterTiming');
    if (character.runes.length != 1) {
      throw const FormatException(
        'characterTiming.character must contain one Unicode code point.',
      );
    }
    final start = _requiredSeconds(json, 'startSeconds', 'characterTiming');
    final end = _requiredSeconds(json, 'endSeconds', 'characterTiming');
    if (end < start) {
      throw const FormatException(
        'characterTiming.endSeconds cannot precede startSeconds.',
      );
    }
    return AvaCharacterTiming(
      character: character,
      start: start,
      end: end,
    );
  }
}

final class AvaCharacterTimingBatchEvent extends AvaPerformanceEvent {
  AvaCharacterTimingBatchEvent._({
    required super.sequence,
    required super.turnId,
    required this.segmentId,
    required this.chunkIndex,
    required List<AvaCharacterTiming> timings,
  })  : timings = List.unmodifiable(timings),
        super(event: PerformanceEventType.characterTimingBatch);

  final String segmentId;
  final int chunkIndex;
  final List<AvaCharacterTiming> timings;

  factory AvaCharacterTimingBatchEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {'segmentId', 'chunkIndex', 'timeBase', 'timings'},
      'characterTimingBatch',
    );
    if (_requiredString(json, 'timeBase', 'characterTimingBatch') !=
        'segmentAudio') {
      throw const FormatException(
        'characterTimingBatch.timeBase must be segmentAudio.',
      );
    }
    final chunkIndex = _requiredInt(json, 'chunkIndex', 'characterTimingBatch');
    final rawTimings = json['timings'];
    if (chunkIndex < 0 || rawTimings is! List || rawTimings.isEmpty) {
      throw const FormatException(
        'characterTimingBatch requires a non-negative chunkIndex and timings.',
      );
    }
    final timings = [
      for (var index = 0; index < rawTimings.length; index += 1)
        AvaCharacterTiming.fromJson(
          _requiredObject(
            rawTimings[index],
            'characterTimingBatch.timings[$index]',
          ),
        ),
    ];
    for (var index = 1; index < timings.length; index += 1) {
      if (timings[index].start < timings[index - 1].start) {
        throw const FormatException(
          'characterTimingBatch timings must be monotonic.',
        );
      }
    }
    return AvaCharacterTimingBatchEvent._(
      sequence: sequence,
      turnId: turnId,
      segmentId: _requiredSegmentId(
        json,
        'segmentId',
        'characterTimingBatch',
      ),
      chunkIndex: chunkIndex,
      timings: timings,
    );
  }
}

final class AvaSegmentBoundaryEvent extends AvaPerformanceEvent {
  const AvaSegmentBoundaryEvent._({
    required super.sequence,
    required super.turnId,
    required this.segmentId,
    required this.segmentIndex,
  }) : super(event: PerformanceEventType.segmentBoundary);

  final String segmentId;
  final int segmentIndex;

  factory AvaSegmentBoundaryEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {'segmentId', 'segmentIndex'},
      'segmentBoundary',
    );
    final segmentIndex = _requiredInt(json, 'segmentIndex', 'segmentBoundary');
    if (segmentIndex < 0 || segmentIndex > 5) {
      throw const FormatException(
        'segmentBoundary.segmentIndex must be between 0 and 5.',
      );
    }
    return AvaSegmentBoundaryEvent._(
      sequence: sequence,
      turnId: turnId,
      segmentId: _requiredSegmentId(json, 'segmentId', 'segmentBoundary'),
      segmentIndex: segmentIndex,
    );
  }
}

final class AvaDiagramCueEvent extends AvaPerformanceEvent {
  const AvaDiagramCueEvent._({
    required super.sequence,
    required super.turnId,
    required this.segmentId,
    required this.cue,
  }) : super(event: PerformanceEventType.diagramCue);

  final String segmentId;
  final AvaDiagramCue cue;

  factory AvaDiagramCueEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {'segmentId', 'cue'},
      'diagramCue',
    );
    return AvaDiagramCueEvent._(
      sequence: sequence,
      turnId: turnId,
      segmentId: _requiredSegmentId(json, 'segmentId', 'diagramCue'),
      cue: AvaDiagramCue.fromJson(
        _requiredObject(json['cue'], 'diagramCue.cue'),
      ),
    );
  }
}

final class AvaTurnCompletedEvent extends AvaPerformanceEvent {
  const AvaTurnCompletedEvent._({
    required super.sequence,
    required super.turnId,
    required this.audioChunkCount,
  }) : super(event: PerformanceEventType.turnCompleted);

  final int audioChunkCount;

  factory AvaTurnCompletedEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(
      json,
      const {'audioChunkCount'},
      'turnCompleted',
    );
    final count = _requiredInt(json, 'audioChunkCount', 'turnCompleted');
    if (count < 1) {
      throw const FormatException(
        'turnCompleted.audioChunkCount must be positive.',
      );
    }
    return AvaTurnCompletedEvent._(
      sequence: sequence,
      turnId: turnId,
      audioChunkCount: count,
    );
  }
}

final class AvaTurnCancelledEvent extends AvaPerformanceEvent {
  const AvaTurnCancelledEvent._({
    required super.sequence,
    required super.turnId,
  }) : super(event: PerformanceEventType.turnCancelled);

  factory AvaTurnCancelledEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(json, const {}, 'turnCancelled');
    return AvaTurnCancelledEvent._(sequence: sequence, turnId: turnId);
  }
}

final class AvaTurnFailedEvent extends AvaPerformanceEvent {
  const AvaTurnFailedEvent._({
    required super.sequence,
    required super.turnId,
    required this.failure,
  }) : super(event: PerformanceEventType.turnFailed);

  final PerformanceFailure failure;

  factory AvaTurnFailedEvent._fromJson(
    Map<String, Object?> json, {
    required int sequence,
    required String turnId,
  }) {
    AvaPerformanceEvent.requireKeys(json, const {'failure'}, 'turnFailed');
    return AvaTurnFailedEvent._(
      sequence: sequence,
      turnId: turnId,
      failure: PerformanceFailure.fromJson(
        _requiredObject(json['failure'], 'turnFailed.failure'),
      ),
    );
  }
}

final RegExp _validSegmentId = RegExp(r'^[a-z][a-z0-9_]{0,39}$');

String _requiredSegmentId(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = _requiredString(json, key, context);
  if (!_validSegmentId.hasMatch(value)) {
    throw FormatException('$context.$key has an invalid segment ID.');
  }
  return value;
}

Duration _requiredSeconds(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = json[key];
  if (value is! num || !value.isFinite || value < 0) {
    throw FormatException('$context.$key must be a finite positive number.');
  }
  return Duration(microseconds: (value.toDouble() * 1000000).round());
}

void _requireExactKeys(
  Map<String, Object?> json,
  Set<String> allowed,
  String context,
) {
  final keys = json.keys.toSet();
  final missing = allowed.difference(keys);
  final unknown = keys.difference(allowed);
  if (missing.isNotEmpty || unknown.isNotEmpty) {
    throw FormatException(
      '$context fields mismatch; missing ${missing.join(', ')}, '
      'unsupported ${unknown.join(', ')}.',
    );
  }
}

String _requiredString(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = json[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _requiredInt(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = json[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}

Map<String, Object?> _requiredObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be a JSON object.');
  }
  return value.cast<String, Object?>();
}

T _enumByName<T extends Enum>(
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
