import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'dart:math';

typedef AvaRealtimeTraceObserver = void Function(AvaRealtimeTraceEvent event);

final class AvaRealtimeTraceEvent {
  const AvaRealtimeTraceEvent({
    required this.attemptId,
    required this.stage,
    required this.elapsed,
    required this.stageDuration,
    required this.fields,
    this.sessionId,
  });

  final String attemptId;
  final String stage;
  final Duration elapsed;
  final Duration stageDuration;
  final Map<String, Object?> fields;
  final String? sessionId;
}

/// One monotonic, correlated timing trace for opening an Ava conversation.
///
/// It intentionally records lifecycle metadata only: never audio, transcripts,
/// provider credentials, prompts, child profile data, or dates of birth.
final class AvaRealtimeTrace {
  AvaRealtimeTrace({
    required this.attemptId,
    AvaRealtimeTraceObserver? observer,
  }) : _observer = observer {
    if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(attemptId)) {
      throw ArgumentError.value(
        attemptId,
        'attemptId',
        'has an invalid format',
      );
    }
    _clock.start();
  }

  static final Object _zoneKey = Object();

  static AvaRealtimeTrace? get current =>
      Zone.current[_zoneKey] as AvaRealtimeTrace?;

  final String attemptId;
  final AvaRealtimeTraceObserver? _observer;
  final Stopwatch _clock = Stopwatch();
  String? _sessionId;

  T bind<T>(T Function() operation) => runZoned(
        operation,
        zoneValues: {_zoneKey: this},
      );

  Future<T> bindAsync<T>(Future<T> Function() operation) => runZoned(
        operation,
        zoneValues: {_zoneKey: this},
      );

  void attachSession(String sessionId) {
    if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(sessionId)) {
      throw ArgumentError.value(
        sessionId,
        'sessionId',
        'has an invalid format',
      );
    }
    final existing = _sessionId;
    if (existing != null && existing != sessionId) {
      throw StateError('A realtime trace cannot change session ownership.');
    }
    _sessionId = sessionId;
  }

  void mark(
    String stage, {
    Duration stageDuration = Duration.zero,
    Map<String, Object?> fields = const {},
  }) {
    final normalized = stage.trim();
    if (!RegExp(r'^[a-z][a-z0-9_.-]{1,63}$').hasMatch(normalized)) {
      throw ArgumentError.value(stage, 'stage', 'has an invalid format');
    }
    final event = AvaRealtimeTraceEvent(
      attemptId: attemptId,
      sessionId: _sessionId,
      stage: normalized,
      elapsed: _clock.elapsed,
      stageDuration: stageDuration,
      fields: Map.unmodifiable(_validatedTraceFields(fields)),
    );
    developer.log(
      'stage=${event.stage} attempt=${event.attemptId} '
      'session=${event.sessionId ?? '-'} '
      'elapsed_ms=${event.elapsed.inMicroseconds / 1000} '
      'stage_ms=${event.stageDuration.inMicroseconds / 1000} '
      'fields=${jsonEncode(event.fields)}',
      name: 'ava.realtime.lifecycle',
    );
    _observer?.call(event);
  }

  Future<T> measure<T>(String stage, Future<T> Function() operation) async {
    final stopwatch = Stopwatch()..start();
    try {
      return await bindAsync(operation);
    } finally {
      stopwatch.stop();
      mark(stage, stageDuration: stopwatch.elapsed);
    }
  }
}

Map<String, Object?> _validatedTraceFields(Map<String, Object?> source) {
  if (source.length > 32) {
    throw ArgumentError.value(source.length, 'fields', 'exceeds 32 entries');
  }
  final result = <String, Object?>{};
  for (final entry in source.entries) {
    if (!RegExp(r'^[A-Za-z][A-Za-z0-9]{0,47}$').hasMatch(entry.key)) {
      throw ArgumentError.value(entry.key, 'fields', 'contains an invalid key');
    }
    final value = entry.value;
    if (value is num && !value.isFinite) {
      throw ArgumentError.value(value, entry.key, 'must be finite');
    }
    if (value is String && value.length > 160) {
      throw ArgumentError.value(
        value.length,
        entry.key,
        'string exceeds 160 characters',
      );
    }
    if (value != null && value is! String && value is! num && value is! bool) {
      throw ArgumentError.value(
        value.runtimeType,
        entry.key,
        'must be a scalar trace value',
      );
    }
    result[entry.key] = value;
  }
  return result;
}

String createSecureAvaRealtimeAttemptId() {
  final random = Random.secure();
  final buffer = StringBuffer('attempt-');
  for (var index = 0; index < 12; index += 1) {
    buffer.write(random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}
