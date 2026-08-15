import 'dart:async';
import 'dart:math' as math;

import 'avatar_action_intent.dart';

typedef AvatarAmbientActionDispatch = Future<void> Function(
  AvatarActionIntent intent,
  Duration now,
);

typedef AvatarAmbientFailureHandler = void Function(
  Object error,
  StackTrace stackTrace,
);

typedef AvatarAmbientTimerFactory = Timer Function(
  Duration duration,
  void Function() callback,
);

Timer _defaultTimerFactory(Duration duration, void Function() callback) =>
    Timer(duration, callback);

/// Schedules sparse, local ambient actions without driving the renderer at
/// frame rate.
///
/// Breathing remains a continuous renderer-owned layer. This controller only
/// emits low-frequency events such as blinks; the native performance graph
/// interpolates their frames and composes them with speech and touch.
class AvatarAmbientMotionController {
  AvatarAmbientMotionController({
    required Duration Function() now,
    required AvatarAmbientActionDispatch dispatch,
    required AvatarAmbientFailureHandler onFailure,
    math.Random? random,
    AvatarAmbientTimerFactory timerFactory = _defaultTimerFactory,
    this.minimumBlinkInterval = const Duration(milliseconds: 2800),
    this.maximumBlinkInterval = const Duration(milliseconds: 6200),
  })  : _now = now,
        _dispatch = dispatch,
        _onFailure = onFailure,
        _random = random ?? math.Random(),
        _timerFactory = timerFactory {
    if (minimumBlinkInterval <= Duration.zero ||
        maximumBlinkInterval < minimumBlinkInterval) {
      throw ArgumentError(
        'Ava blink interval must be positive and ordered.',
      );
    }
  }

  final Duration Function() _now;
  final AvatarAmbientActionDispatch _dispatch;
  final AvatarAmbientFailureHandler _onFailure;
  final math.Random _random;
  final AvatarAmbientTimerFactory _timerFactory;
  final Duration minimumBlinkInterval;
  final Duration maximumBlinkInterval;

  Timer? _timer;
  int _sequence = 0;
  bool _running = false;
  bool _failed = false;
  bool _disposed = false;

  bool get isRunning => _running && !_failed && !_disposed;

  void start() {
    _ensureNotDisposed();
    if (_running) {
      throw StateError('Ava ambient motion is already running.');
    }
    if (_failed) {
      throw StateError('Ava ambient motion failed and cannot be restarted.');
    }
    _running = true;
    _scheduleNextBlink();
  }

  void stop() {
    if (_disposed) return;
    _running = false;
    _timer?.cancel();
    _timer = null;
  }

  void dispose() {
    if (_disposed) return;
    stop();
    _disposed = true;
  }

  void _scheduleNextBlink() {
    if (!isRunning) return;
    final minimum = minimumBlinkInterval.inMicroseconds;
    final span = maximumBlinkInterval.inMicroseconds -
        minimumBlinkInterval.inMicroseconds;
    final interval = Duration(
      microseconds: minimum + (span * _random.nextDouble()).round(),
    );
    _timer = _timerFactory(interval, () {
      _timer = null;
      unawaited(_blink());
    });
  }

  Future<void> _blink() async {
    if (!isRunning) return;
    final occurredAt = _now();
    _sequence += 1;
    try {
      await _dispatch(
        AvatarActionIntent(
          id: 'ambient:blink:$_sequence',
          kind: AvatarActionKind.blink,
          source: AvatarActionSource.idleSystem,
          priority: AvatarActionPriority.ambient,
          channelClaims: const {
            AvatarMotionChannel.faceExpression: AvatarMotionBlendMode.override,
          },
          occurredAt: occurredAt,
          blendIn: const Duration(milliseconds: 24),
          blendOut: const Duration(milliseconds: 36),
          expectedDuration: const Duration(milliseconds: 180),
          interruptionPolicy: AvatarInterruptionPolicy.interruptible,
          retargetPolicy: AvatarRetargetPolicy.never,
        ),
        occurredAt,
      );
      _scheduleNextBlink();
    } catch (error, stackTrace) {
      _failed = true;
      _running = false;
      _timer?.cancel();
      _timer = null;
      _onFailure(error, stackTrace);
    }
  }

  void _ensureNotDisposed() {
    if (_disposed) {
      throw StateError('Ava ambient motion controller is disposed.');
    }
  }
}
