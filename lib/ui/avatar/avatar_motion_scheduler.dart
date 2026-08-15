import 'dart:collection';

import 'avatar_action_intent.dart';

enum AvatarScheduleResultType {
  started,
  retargeted,
  replaced,
  queued,
  queueCoalesced,
}

class AvatarScheduledAction {
  const AvatarScheduledAction._({
    required this.runtimeId,
    required this.intent,
    required this.startedAt,
    required this.updatedAt,
    required this.revision,
  });

  factory AvatarScheduledAction.start(
    AvatarActionIntent intent,
    Duration now,
  ) {
    return AvatarScheduledAction._(
      runtimeId: intent.id,
      intent: intent,
      startedAt: now,
      updatedAt: now,
      revision: 0,
    );
  }

  final String runtimeId;
  final AvatarActionIntent intent;
  final Duration startedAt;
  final Duration updatedAt;

  /// Increases when an active action is steered without restarting it.
  final int revision;

  Duration elapsedAt(Duration now) => now - startedAt;

  bool canBeInterruptedAt(Duration now) {
    return switch (intent.interruptionPolicy) {
      AvatarInterruptionPolicy.interruptible => true,
      AvatarInterruptionPolicy.afterMinimumHold =>
        elapsedAt(now) >= intent.minimumHold,
      AvatarInterruptionPolicy.mustComplete => false,
    };
  }

  bool canRetargetTo(AvatarActionIntent next, Duration now) {
    if (!intent.canRetarget(next)) return false;
    return switch (intent.retargetPolicy) {
      AvatarRetargetPolicy.never => false,
      AvatarRetargetPolicy.immediate => true,
      AvatarRetargetPolicy.afterMinimumHold =>
        elapsedAt(now) >= intent.minimumHold,
    };
  }

  AvatarScheduledAction retarget(
    AvatarActionIntent next,
    Duration now,
  ) {
    return AvatarScheduledAction._(
      runtimeId: runtimeId,
      intent: next,
      startedAt: startedAt,
      updatedAt: now,
      revision: revision + 1,
    );
  }
}

class AvatarScheduleResult {
  const AvatarScheduleResult({
    required this.type,
    this.activeAction,
    this.stoppedActions = const [],
    this.queuedIntent,
    this.coalescedIntent,
  });

  final AvatarScheduleResultType type;
  final AvatarScheduledAction? activeAction;
  final List<AvatarScheduledAction> stoppedActions;
  final AvatarActionIntent? queuedIntent;
  final AvatarActionIntent? coalescedIntent;
}

class AvatarCompletionResult {
  const AvatarCompletionResult({
    required this.completedAction,
    required this.startedActions,
  });

  final AvatarScheduledAction completedAction;
  final List<AvatarScheduledAction> startedActions;
}

class AvatarCancellationResult {
  const AvatarCancellationResult({
    required this.cancelledActions,
    required this.startedActions,
  });

  final List<AvatarScheduledAction> cancelledActions;
  final List<AvatarScheduledAction> startedActions;
}

/// Resolves concurrent semantic actions before any renderer sees them.
///
/// The scheduler is deterministic and uses a monotonic caller-owned clock. It
/// never guesses that a renderer action completed; the renderer must explicitly
/// call [complete].
class AvatarMotionScheduler {
  final Map<String, AvatarScheduledAction> _activeByRuntimeId = {};
  final List<AvatarActionIntent> _queued = [];
  final Set<String> _seenIntentIds = {};

  UnmodifiableListView<AvatarScheduledAction> get activeActions =>
      UnmodifiableListView(_activeByRuntimeId.values);

  UnmodifiableListView<AvatarActionIntent> get queuedIntents =>
      UnmodifiableListView(_queued);

  AvatarScheduleResult submit(
    AvatarActionIntent intent, {
    required Duration now,
  }) {
    _validateNow(now);
    if (!_seenIntentIds.add(intent.id)) {
      throw StateError(
          'Avatar action intent ${intent.id} was submitted twice.');
    }

    final conflicts = _conflictsFor(intent);
    if (conflicts.isEmpty) {
      final action = _start(intent, now);
      return AvatarScheduleResult(
        type: AvatarScheduleResultType.started,
        activeAction: action,
      );
    }

    if (conflicts.length == 1) {
      final current = conflicts.single;
      if (current.canRetargetTo(intent, now)) {
        final updated = current.retarget(intent, now);
        _activeByRuntimeId[current.runtimeId] = updated;
        return AvatarScheduleResult(
          type: AvatarScheduleResultType.retargeted,
          activeAction: updated,
        );
      }
    }

    final canReplace = conflicts.every(
      (active) =>
          intent.priority.value > active.intent.priority.value &&
          active.canBeInterruptedAt(now),
    );
    if (canReplace) {
      for (final active in conflicts) {
        _activeByRuntimeId.remove(active.runtimeId);
      }
      final action = _start(intent, now);
      return AvatarScheduleResult(
        type: AvatarScheduleResultType.replaced,
        activeAction: action,
        stoppedActions: List.unmodifiable(conflicts),
      );
    }

    final coalescedIndex = _queued.indexWhere(
      (queued) => queued.canRetarget(intent),
    );
    if (coalescedIndex >= 0) {
      final previous = _queued[coalescedIndex];
      _queued[coalescedIndex] = intent;
      _sortQueue();
      return AvatarScheduleResult(
        type: AvatarScheduleResultType.queueCoalesced,
        queuedIntent: intent,
        coalescedIntent: previous,
      );
    }

    _queued.add(intent);
    _sortQueue();
    return AvatarScheduleResult(
      type: AvatarScheduleResultType.queued,
      queuedIntent: intent,
    );
  }

  AvatarCompletionResult complete(
    String runtimeId, {
    required Duration now,
  }) {
    _validateNow(now);
    final completed = _activeByRuntimeId.remove(runtimeId);
    if (completed == null) {
      throw StateError(
        'Cannot complete unknown avatar action runtime $runtimeId.',
      );
    }
    final started = _promoteQueued(now);
    return AvatarCompletionResult(
      completedAction: completed,
      startedActions: List.unmodifiable(started),
    );
  }

  AvatarCancellationResult cancelSource(
    AvatarActionSource source, {
    required Duration now,
  }) {
    _validateNow(now);
    final cancelled = _activeByRuntimeId.values
        .where((action) => action.intent.source == source)
        .toList(growable: false);
    for (final action in cancelled) {
      _activeByRuntimeId.remove(action.runtimeId);
    }
    _queued.removeWhere((intent) => intent.source == source);
    final started = _promoteQueued(now);
    return AvatarCancellationResult(
      cancelledActions: List.unmodifiable(cancelled),
      startedActions: List.unmodifiable(started),
    );
  }

  void reset() {
    _activeByRuntimeId.clear();
    _queued.clear();
    _seenIntentIds.clear();
  }

  AvatarScheduledAction _start(AvatarActionIntent intent, Duration now) {
    final action = AvatarScheduledAction.start(intent, now);
    _activeByRuntimeId[action.runtimeId] = action;
    return action;
  }

  List<AvatarScheduledAction> _conflictsFor(AvatarActionIntent intent) {
    return _activeByRuntimeId.values
        .where(
          (active) =>
              active.intent.conflictsWith(intent) ||
              active.intent.canRetarget(intent),
        )
        .toList(growable: false);
  }

  List<AvatarScheduledAction> _promoteQueued(Duration now) {
    final started = <AvatarScheduledAction>[];
    var index = 0;
    while (index < _queued.length) {
      final intent = _queued[index];
      if (_conflictsFor(intent).isNotEmpty) {
        index += 1;
        continue;
      }
      _queued.removeAt(index);
      started.add(_start(intent, now));
    }
    return started;
  }

  void _sortQueue() {
    _queued.sort((left, right) {
      final byPriority = right.priority.value.compareTo(left.priority.value);
      if (byPriority != 0) return byPriority;
      final byTime = left.occurredAt.compareTo(right.occurredAt);
      if (byTime != 0) return byTime;
      return left.id.compareTo(right.id);
    });
  }

  void _validateNow(Duration now) {
    if (now.isNegative) {
      throw ArgumentError.value(now, 'now', 'must not be negative');
    }
  }
}
