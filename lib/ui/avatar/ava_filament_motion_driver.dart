import 'dart:async';
import 'dart:collection';
import 'dart:math' as math;

import 'avatar_motion_driver.dart';
import 'avatar_motion_scheduler.dart';

typedef AvaMotionTimerFactory = Timer Function(
  Duration duration,
  void Function() callback,
);

typedef AvaMotionClock = Duration Function();

Timer _defaultTimerFactory(Duration duration, void Function() callback) {
  return Timer(duration, callback);
}

/// Confirmation that the renderer accepted one complete runtime layer.
class AvaRendererLayerReceipt {
  AvaRendererLayerReceipt({
    required this.runtimeId,
    required this.revision,
    required this.completesAfter,
  }) {
    if (runtimeId.trim().isEmpty) {
      throw ArgumentError.value(runtimeId, 'runtimeId', 'must not be empty');
    }
    if (revision < 0) {
      throw ArgumentError.value(revision, 'revision', 'must not be negative');
    }
    final duration = completesAfter;
    if (duration != null && duration <= Duration.zero) {
      throw ArgumentError.value(
        duration,
        'completesAfter',
        'must be positive when supplied',
      );
    }
  }

  final String runtimeId;
  final int revision;

  /// Null means the renderer layer loops until explicitly stopped.
  final Duration? completesAfter;
}

/// Narrow native-renderer surface used by [AvaFilamentMotionDriver].
///
/// The sink owns the final pose. Callers provide scheduled semantic actions;
/// they never write bones or morph targets directly.
abstract interface class AvaRendererMotionLayerSink {
  bool get isMotionLayerReady;
  AvatarMotionDriverCapabilities get motionCapabilities;

  Future<AvaRendererLayerReceipt> startMotionLayer(
    AvatarScheduledAction action,
  );

  Future<AvaRendererLayerReceipt> retargetMotionLayer(
    AvatarScheduledAction action,
  );

  Future<void> stopMotionLayer(
    String runtimeId, {
    required Duration blendOut,
    required Duration occurredAt,
  });

  Future<void> completeMotionLayer(
    String runtimeId, {
    required int revision,
    required Duration occurredAt,
  });

  Future<void> clearMotionLayers();
}

/// Production motion-driver adapter for Ava's Filament renderer.
///
/// It serializes runtime-layer ownership through the renderer sink, rejects
/// stale revisions, and emits completion only after the renderer has removed
/// the matching layer. Timers represent the accepted native procedural motion
/// duration; they are not animation guesses made by the behavior layer.
class AvaFilamentMotionDriver implements AvatarMotionDriver {
  static const _maximumCompletionTombstones = 64;

  AvaFilamentMotionDriver({
    required AvaRendererMotionLayerSink sink,
    AvaMotionClock? clock,
    AvaMotionTimerFactory timerFactory = _defaultTimerFactory,
    int maximumConcurrentActions = 16,
  })  : _sink = sink,
        _timerFactory = timerFactory,
        _maximumConcurrentActions = maximumConcurrentActions,
        _clock = clock ?? _defaultClock() {
    if (maximumConcurrentActions < 8) {
      throw ArgumentError.value(
        maximumConcurrentActions,
        'maximumConcurrentActions',
        'must satisfy Ava\'s minimum of eight concurrent actions',
      );
    }
  }

  static AvaMotionClock _defaultClock() {
    final stopwatch = Stopwatch()..start();
    return () => stopwatch.elapsed;
  }

  final AvaRendererMotionLayerSink _sink;
  final AvaMotionClock _clock;
  final AvaMotionTimerFactory _timerFactory;
  final int _maximumConcurrentActions;
  final StreamController<AvatarMotionDriverEvent> _events =
      StreamController<AvatarMotionDriverEvent>.broadcast(sync: true);
  final Map<String, _AvaLayerPlayback> _active = {};
  final Map<String, int> _pendingRetargetRevisions = {};
  final Set<String> _pendingStops = {};
  final Set<String> _nativeRetiredForRetarget = {};
  final LinkedHashSet<String> _recentlyCompleted = LinkedHashSet();
  Future<void> _operationTail = Future<void>.value();

  Duration _lastTimelineAt = Duration.zero;
  bool _initialized = false;
  bool _disposed = false;

  @override
  AvatarMotionDriverCapabilities get capabilities {
    final native = _sink.motionCapabilities;
    return AvatarMotionDriverCapabilities(
      layeredSkeletalAnimation: native.layeredSkeletalAnimation,
      additiveAnimation: native.additiveAnimation,
      proceduralBoneControl: native.proceduralBoneControl,
      morphTargetControl: native.morphTargetControl,
      skinnedSurfacePicking: native.skinnedSurfacePicking,
      cameraPoseReporting: native.cameraPoseReporting,
      maximumConcurrentActions: math.min(
        native.maximumConcurrentActions,
        _maximumConcurrentActions,
      ),
    );
  }

  @override
  Stream<AvatarMotionDriverEvent> get events => _events.stream;

  int get activeLayerCount => _active.length;

  @override
  Future<void> initialize() async {
    _ensureNotDisposed();
    if (_initialized) {
      throw StateError('Ava Filament motion driver is already initialized.');
    }
    if (!_sink.isMotionLayerReady) {
      throw StateError(
        'Ava Filament renderer is not attached to a validated rig.',
      );
    }
    if (!capabilities.supportsProductionInteraction) {
      throw UnsupportedError(
        'Ava Filament renderer did not validate the production motion '
        'contract.',
      );
    }
    _initialized = true;
  }

  @override
  Future<void> start(AvatarScheduledAction action) {
    _ensureReady();
    return _enqueueOperation(() async {
      if (_active.containsKey(action.runtimeId)) {
        throw StateError(
          'Ava renderer layer ${action.runtimeId} is already active.',
        );
      }
      if (_active.length >= _maximumConcurrentActions) {
        throw StateError(
          'Ava renderer reached its $_maximumConcurrentActions-layer limit.',
        );
      }

      _recentlyCompleted.remove(action.runtimeId);
      final receipt = await _sink.startMotionLayer(action);
      _validateReceipt(action, receipt);
      _installPlayback(action, receipt);
    });
  }

  @override
  Future<void> retarget(AvatarScheduledAction action) {
    _ensureReady();
    final current = _active[action.runtimeId];
    if (current == null) {
      throw StateError(
        'Cannot retarget unknown Ava renderer layer ${action.runtimeId}.',
      );
    }
    final requestedRevision =
        _pendingRetargetRevisions[action.runtimeId] ?? current.revision;
    if (action.revision <= requestedRevision) {
      throw StateError(
        'Ava renderer layer ${action.runtimeId} received stale revision '
        '${action.revision} after $requestedRevision.',
      );
    }

    current.timer?.cancel();
    _pendingRetargetRevisions[action.runtimeId] = action.revision;
    return _enqueueOperation(() async {
      final active = _active[action.runtimeId];
      if (active == null) {
        throw StateError(
          'Cannot retarget unknown Ava renderer layer ${action.runtimeId}.',
        );
      }
      try {
        final receipt = _nativeRetiredForRetarget.remove(action.runtimeId)
            ? await _sink.startMotionLayer(action)
            : await _sink.retargetMotionLayer(action);
        _validateReceipt(action, receipt);
        _installPlayback(action, receipt);
        if (_pendingRetargetRevisions[action.runtimeId] == action.revision) {
          _pendingRetargetRevisions.remove(action.runtimeId);
        }
      } catch (_) {
        _pendingRetargetRevisions.remove(action.runtimeId);
        _active.remove(action.runtimeId)?.timer?.cancel();
        rethrow;
      }
    });
  }

  @override
  Future<void> stop(
    String runtimeId, {
    required Duration blendOut,
  }) {
    _ensureReady();
    if (blendOut.isNegative) {
      throw ArgumentError.value(blendOut, 'blendOut', 'must not be negative');
    }
    final playback = _active[runtimeId];
    if (playback == null) {
      if (_recentlyCompleted.remove(runtimeId)) {
        return Future<void>.value();
      }
      throw StateError('Cannot stop unknown Ava renderer layer $runtimeId.');
    }
    playback.timer?.cancel();
    _pendingStops.add(runtimeId);
    return _enqueueOperation(() async {
      final current = _active.remove(runtimeId);
      if (current == null) {
        if (_recentlyCompleted.remove(runtimeId)) {
          _pendingStops.remove(runtimeId);
          return;
        }
        _pendingStops.remove(runtimeId);
        throw StateError('Cannot stop unknown Ava renderer layer $runtimeId.');
      }
      current.timer?.cancel();
      _pendingRetargetRevisions.remove(runtimeId);
      try {
        if (!_nativeRetiredForRetarget.remove(runtimeId)) {
          await _sink.stopMotionLayer(
            runtimeId,
            blendOut: blendOut,
            occurredAt: _timelineNow(),
          );
        }
      } finally {
        _pendingStops.remove(runtimeId);
      }
    });
  }

  void _installPlayback(
    AvatarScheduledAction action,
    AvaRendererLayerReceipt receipt,
  ) {
    final duration = receipt.completesAfter;
    Timer? timer;
    if (duration != null) {
      final runtimeId = receipt.runtimeId;
      final revision = receipt.revision;
      timer = _timerFactory(
        duration,
        () => unawaited(_complete(runtimeId, revision)),
      );
    }
    final completionAt = duration == null ? null : action.updatedAt + duration;
    if (action.updatedAt > _lastTimelineAt) {
      _lastTimelineAt = action.updatedAt;
    }
    _active[action.runtimeId] = _AvaLayerPlayback(
      revision: action.revision,
      timer: timer,
      completionAt: completionAt,
    );
  }

  Future<void> _complete(String runtimeId, int revision) async {
    if (_disposed || !_initialized) return;
    await _enqueueOperation(() async {
      final playback = _active[runtimeId];
      if (playback == null || playback.revision != revision) return;
      final requestedRevision = _pendingRetargetRevisions[runtimeId];
      if (_pendingStops.contains(runtimeId) ||
          (requestedRevision != null && requestedRevision > revision)) {
        return;
      }

      try {
        final occurredAt = _timelineNow(playback.completionAt);
        await _sink.completeMotionLayer(
          runtimeId,
          revision: revision,
          occurredAt: occurredAt,
        );
        final pendingRevision = _pendingRetargetRevisions[runtimeId];
        if (pendingRevision != null && pendingRevision > revision) {
          _nativeRetiredForRetarget.add(runtimeId);
          return;
        }
        if (_pendingStops.contains(runtimeId)) {
          _active.remove(runtimeId);
          _rememberCompletion(runtimeId);
          return;
        }
        final latest = _active[runtimeId];
        if (latest == null || latest.revision != revision) return;
        _active.remove(runtimeId);
        _rememberCompletion(runtimeId);
        _events.add(
          AvatarMotionCompleted(
            runtimeId: runtimeId,
            occurredAt: occurredAt,
          ),
        );
      } catch (error) {
        _active.remove(runtimeId)?.timer?.cancel();
        _events.add(
          AvatarMotionFailed(runtimeId: runtimeId, message: error.toString()),
        );
      }
    });
  }

  Future<T> _enqueueOperation<T>(Future<T> Function() operation) {
    final completion = Completer<T>();
    _operationTail = _operationTail.then((_) async {
      try {
        completion.complete(await operation());
      } catch (error, stackTrace) {
        completion.completeError(error, stackTrace);
      }
    });
    return completion.future;
  }

  void _rememberCompletion(String runtimeId) {
    _recentlyCompleted
      ..remove(runtimeId)
      ..add(runtimeId);
    while (_recentlyCompleted.length > _maximumCompletionTombstones) {
      _recentlyCompleted.remove(_recentlyCompleted.first);
    }
  }

  Duration _timelineNow([Duration? lowerBound]) {
    var now = _clock();
    if (now < _lastTimelineAt) now = _lastTimelineAt;
    if (lowerBound != null && now < lowerBound) now = lowerBound;
    _lastTimelineAt = now;
    return now;
  }

  void _validateReceipt(
    AvatarScheduledAction action,
    AvaRendererLayerReceipt receipt,
  ) {
    if (receipt.runtimeId != action.runtimeId ||
        receipt.revision != action.revision) {
      throw StateError(
        'Ava renderer acknowledged ${receipt.runtimeId}@${receipt.revision} '
        'for ${action.runtimeId}@${action.revision}.',
      );
    }
  }

  @override
  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    _initialized = false;
    for (final playback in _active.values) {
      playback.timer?.cancel();
    }
    await _operationTail;
    _active.clear();
    _pendingRetargetRevisions.clear();
    _pendingStops.clear();
    _nativeRetiredForRetarget.clear();
    _recentlyCompleted.clear();
    try {
      if (_sink.isMotionLayerReady) {
        await _sink.clearMotionLayers();
      }
    } finally {
      await _events.close();
    }
  }

  void _ensureReady() {
    _ensureNotDisposed();
    if (!_initialized) {
      throw StateError('Ava Filament motion driver is not initialized.');
    }
    if (!_sink.isMotionLayerReady) {
      throw StateError('Ava Filament renderer detached during performance.');
    }
  }

  void _ensureNotDisposed() {
    if (_disposed) {
      throw StateError('Ava Filament motion driver is disposed.');
    }
  }
}

class _AvaLayerPlayback {
  const _AvaLayerPlayback({
    required this.revision,
    required this.timer,
    required this.completionAt,
  });

  final int revision;
  final Timer? timer;
  final Duration? completionAt;
}
