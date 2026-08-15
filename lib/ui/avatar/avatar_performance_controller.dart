import 'dart:async';
import 'dart:collection';

import 'avatar_action_intent.dart';
import 'avatar_motion_driver.dart';
import 'avatar_motion_scheduler.dart';

typedef AvatarPerformanceFailureHandler = void Function(
  Object error,
  StackTrace stackTrace,
);

/// Applies the deterministic motion schedule to a production-capable renderer.
class AvatarPerformanceController {
  static const _maximumCancellationTombstones = 64;

  AvatarPerformanceController({
    required AvatarMotionDriver driver,
    required AvatarPerformanceFailureHandler onFailure,
    AvatarMotionScheduler? scheduler,
  })  : _driver = driver,
        _onFailure = onFailure,
        _scheduler = scheduler ?? AvatarMotionScheduler();

  final AvatarMotionDriver _driver;
  final AvatarPerformanceFailureHandler _onFailure;
  final AvatarMotionScheduler _scheduler;
  final LinkedHashSet<String> _cancelledRuntimeIds = LinkedHashSet();

  StreamSubscription<AvatarMotionDriverEvent>? _eventSubscription;
  bool _initialized = false;
  bool _failed = false;
  bool _disposed = false;

  bool get isReady => _initialized && !_failed && !_disposed;
  List<AvatarScheduledAction> get activeActions =>
      List.unmodifiable(_scheduler.activeActions);
  List<AvatarActionIntent> get queuedIntents =>
      List.unmodifiable(_scheduler.queuedIntents);

  Future<void> initialize() async {
    _ensureNotDisposed();
    if (_initialized) {
      throw StateError('Ava performance controller is already initialized.');
    }
    final capabilities = _driver.capabilities;
    if (!capabilities.supportsProductionInteraction) {
      throw UnsupportedError(
        'Ava renderer does not satisfy the production interaction contract: '
        'layered=${capabilities.layeredSkeletalAnimation}, '
        'additive=${capabilities.additiveAnimation}, '
        'bones=${capabilities.proceduralBoneControl}, '
        'morphs=${capabilities.morphTargetControl}, '
        'picking=${capabilities.skinnedSurfacePicking}, '
        'camera=${capabilities.cameraPoseReporting}, '
        'concurrent=${capabilities.maximumConcurrentActions}.',
      );
    }

    _eventSubscription = _driver.events.listen(
      (event) => unawaited(_handleDriverEvent(event)),
      onError: (Object error, StackTrace stackTrace) {
        _recordFailure(error, stackTrace);
      },
    );
    try {
      await _driver.initialize();
      _initialized = true;
    } catch (error, stackTrace) {
      _recordFailure(error, stackTrace);
      rethrow;
    }
  }

  Future<AvatarScheduleResult> dispatch(
    AvatarActionIntent intent, {
    required Duration now,
  }) async {
    _ensureReady();
    final result = _scheduler.submit(intent, now: now);
    try {
      switch (result.type) {
        case AvatarScheduleResultType.started:
          await _driver.start(result.activeAction!);
        case AvatarScheduleResultType.retargeted:
          await _driver.retarget(result.activeAction!);
        case AvatarScheduleResultType.replaced:
          for (final stopped in result.stoppedActions) {
            await _driver.stop(
              stopped.runtimeId,
              blendOut: stopped.intent.blendOut,
            );
          }
          await _driver.start(result.activeAction!);
        case AvatarScheduleResultType.queued:
        case AvatarScheduleResultType.queueCoalesced:
          break;
      }
      return result;
    } catch (error, stackTrace) {
      _recordFailure(error, stackTrace);
      rethrow;
    }
  }

  Future<AvatarCancellationResult> cancelSource(
    AvatarActionSource source, {
    required Duration now,
  }) async {
    _ensureReady();
    final result = _scheduler.cancelSource(source, now: now);
    for (final cancelled in result.cancelledActions) {
      _rememberCancelledRuntime(cancelled.runtimeId);
    }
    try {
      for (final cancelled in result.cancelledActions) {
        await _driver.stop(
          cancelled.runtimeId,
          blendOut: cancelled.intent.blendOut,
        );
      }
      for (final started in result.startedActions) {
        await _driver.start(started);
      }
      return result;
    } catch (error, stackTrace) {
      _recordFailure(error, stackTrace);
      rethrow;
    }
  }

  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    _initialized = false;
    await _eventSubscription?.cancel();
    _eventSubscription = null;
    _scheduler.reset();
    _cancelledRuntimeIds.clear();
    await _driver.dispose();
  }

  Future<void> _handleDriverEvent(AvatarMotionDriverEvent event) async {
    if (!isReady) return;
    if (event is AvatarMotionFailed) {
      _recordFailure(
        StateError(
          'Avatar renderer failed action ${event.runtimeId}: ${event.message}',
        ),
        StackTrace.current,
      );
      return;
    }
    if (event is! AvatarMotionCompleted) {
      _recordFailure(
        StateError('Unknown avatar renderer event ${event.runtimeType}.'),
        StackTrace.current,
      );
      return;
    }
    if (_cancelledRuntimeIds.remove(event.runtimeId)) {
      return;
    }

    try {
      final result = _scheduler.complete(
        event.runtimeId,
        now: event.occurredAt,
      );
      for (final action in result.startedActions) {
        await _driver.start(action);
      }
    } catch (error, stackTrace) {
      _recordFailure(error, stackTrace);
    }
  }

  void _recordFailure(Object error, StackTrace stackTrace) {
    if (_failed || _disposed) return;
    _failed = true;
    _onFailure(error, stackTrace);
  }

  void _rememberCancelledRuntime(String runtimeId) {
    _cancelledRuntimeIds
      ..remove(runtimeId)
      ..add(runtimeId);
    while (_cancelledRuntimeIds.length > _maximumCancellationTombstones) {
      _cancelledRuntimeIds.remove(_cancelledRuntimeIds.first);
    }
  }

  void _ensureReady() {
    _ensureNotDisposed();
    if (!_initialized || _failed) {
      throw StateError('Ava performance controller is not ready.');
    }
  }

  void _ensureNotDisposed() {
    if (_disposed) {
      throw StateError('Ava performance controller is disposed.');
    }
  }
}
