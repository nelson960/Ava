import 'dart:async';
import 'dart:developer' as developer;
import 'dart:math';

typedef AvaRealtimeTraceObserver = void Function(AvaRealtimeTraceEvent event);

final class AvaRealtimeTraceEvent {
  const AvaRealtimeTraceEvent({
    required this.attemptId,
    required this.stage,
    required this.elapsed,
    required this.stageDuration,
    this.sessionId,
  });

  final String attemptId;
  final String stage;
  final Duration elapsed;
  final Duration stageDuration;
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

  void mark(String stage, {Duration stageDuration = Duration.zero}) {
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
    );
    developer.log(
      'stage=${event.stage} attempt=${event.attemptId} '
      'session=${event.sessionId ?? '-'} '
      'elapsed_ms=${event.elapsed.inMicroseconds / 1000} '
      'stage_ms=${event.stageDuration.inMicroseconds / 1000}',
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

String createSecureAvaRealtimeAttemptId() {
  final random = Random.secure();
  final buffer = StringBuffer('attempt-');
  for (var index = 0; index < 12; index += 1) {
    buffer.write(random.nextInt(256).toRadixString(16).padLeft(2, '0'));
  }
  return buffer.toString();
}
