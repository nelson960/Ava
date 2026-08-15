import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../canvas/ava_canvas_engine.dart';
import '../models/ava_realtime_protocol.dart';
import '../services/ava_realtime_presentation.dart';
import '../services/ava_realtime_runtime_factory.dart';
import '../services/ava_realtime_turn_runtime.dart';
import 'ava_session_state.dart';

/// Owns one persistent protocol-v3 conversation connection.
///
/// Connection ownership and microphone intent are deliberately independent:
/// startup prepares the muted WebRTC call, while microphone taps only toggle
/// the already-attached audio track. Turn completion never changes the user's
/// microphone choice.
final class AvaRealtimeSessionController
    extends StateNotifier<AvaSessionState> {
  AvaRealtimeSessionController({
    required AvaRealtimeRuntimeFactory runtimeFactory,
    required AvaRealtimePresentationController presentation,
    required AvaCanvasEngine canvasEngine,
    required AvaRealtimeAgeBand Function() ageBand,
    required double Function() volume,
    Duration warmRetention = const Duration(minutes: 2),
  })  : _runtimeFactory = runtimeFactory,
        _presentation = presentation,
        _canvasEngine = canvasEngine,
        _ageBand = ageBand,
        _volume = volume,
        _warmRetention = warmRetention,
        super(const AvaSessionState()) {
    if (warmRetention <= Duration.zero ||
        warmRetention > const Duration(minutes: 10)) {
      throw RangeError.range(
        warmRetention.inMilliseconds,
        1,
        const Duration(minutes: 10).inMilliseconds,
        'warmRetention',
      );
    }
    _presentation.addListener(_handlePresentationChanged);
    _canvasEngine.addListener(_handleCanvasChanged);
  }

  final AvaRealtimeRuntimeFactory _runtimeFactory;
  final AvaRealtimePresentationController _presentation;
  final AvaCanvasEngine _canvasEngine;
  final AvaRealtimeAgeBand Function() _ageBand;
  final double Function() _volume;
  final Duration _warmRetention;

  AvaRealtimeTurnRuntime? _runtime;
  Future<void>? _runFuture;
  Future<void>? _preparing;
  Timer? _warmExpiry;
  int _connectionEpoch = 0;
  int _intentRevision = 0;
  bool _disposed = false;
  int _lastLoggedTerminalRevision = -1;

  /// Opens the complete muted conversation during app startup.
  ///
  /// Concurrent callers share one operation. Completion means bootstrap, SDP,
  /// ICE, sideband, event WebSocket, clientReady, and the first validated
  /// listening phase all succeeded while the local track remained disabled.
  Future<void> prepare() {
    _requireActiveController();
    if (_runtime != null) return Future<void>.value();
    final activePreparation = _preparing;
    if (activePreparation != null) return activePreparation;

    state = state.copyWith(
      connectionHealth: AvaConnectionHealth.preparing,
      clearError: true,
    );

    final epoch = _connectionEpoch;
    late final Future<void> operation;
    operation = _prepareConnection(epoch).whenComplete(() {
      if (identical(_preparing, operation)) _preparing = null;
    });
    _preparing = operation;
    return operation;
  }

  /// Changes only the user's microphone intent and the persistent audio track.
  Future<void> setMicrophoneActive(bool active) async {
    _requireActiveController();
    final revision = ++_intentRevision;
    _warmExpiry?.cancel();
    _warmExpiry = null;

    state = state.copyWith(
      turnState: active ? AvaSessionPhase.listening : AvaSessionPhase.idle,
      userMicIntent: active,
      clearActiveSegment: !active,
      clearError: true,
    );

    if (!active) {
      final runtime = _runtime;
      if (runtime == null) return;
      try {
        await runtime.pauseConversation(reason: 'userMutedMicrophone');
      } catch (error, stackTrace) {
        await _failConnection(
          runtime: runtime,
          epoch: _connectionEpoch,
          error: error,
          stackTrace: stackTrace,
        );
        Error.throwWithStackTrace(error, stackTrace);
      }
      if (!_disposed &&
          revision == _intentRevision &&
          !state.userMicIntent &&
          identical(_runtime, runtime)) {
        _scheduleWarmExpiry(runtime);
      }
      return;
    }

    try {
      await prepare();
      if (_disposed || revision != _intentRevision || !state.userMicIntent) {
        return;
      }
      final runtime = _runtime;
      if (runtime == null) {
        throw StateError(
          'Ava conversation preparation completed without a runtime.',
        );
      }
      await runtime.resumeConversation();
      developer.log(
        'microphone_unmuted epoch=$_connectionEpoch',
        name: 'ava.realtime',
      );
    } catch (error, stackTrace) {
      if (!_disposed && revision == _intentRevision) {
        state = state.copyWith(
          turnState: AvaSessionPhase.failure,
          userMicIntent: false,
          actualTrackEnabled: false,
          connectionHealth: AvaConnectionHealth.failed,
          errorMessage: _messageFor(error),
        );
      }
      Error.throwWithStackTrace(error, stackTrace);
    }
  }

  Future<void> _prepareConnection(int epoch) async {
    AvaRealtimeTurnRuntime? runtime;
    Future<void>? runFuture;
    try {
      final presentationState = _presentation.state;
      if (!presentationState.isActive && presentationState.turnId != null) {
        _presentation.reset();
      }
      // Canvas lifetime matches the newly-created realtime session. A fresh
      // WebRTC/session bootstrap must not display or submit bindings from the
      // previous canonical server scene.
      _canvasEngine.clear();
      runtime = await _runtimeFactory.create(
        ageBand: _ageBand(),
        volume: _volume(),
        onMicrophoneTrackChanged: (enabled) =>
            _handleMicrophoneTrackChanged(epoch, enabled),
      );
      if (_isStale(epoch)) {
        await runtime.close();
        return;
      }

      _runtime = runtime;
      runFuture = runtime.run();
      _runFuture = runFuture;
      await Future.any<void>([
        runtime.firstListening,
        runFuture.then<void>((_) {
          if (!_isStale(epoch)) {
            throw const AvaRealtimeRuntimeException(
              'Ava conversation ended before its first listening phase.',
            );
          }
        }),
      ]);
      if (_isStale(epoch) || !identical(_runtime, runtime)) {
        await runtime.close();
        return;
      }
      if (runtime.microphoneTrackEnabled) {
        throw const AvaRealtimeRuntimeException(
          'Ava startup completed with the microphone track enabled.',
        );
      }

      developer.log(
        'conversation_prepared epoch=$epoch microphone=false',
        name: 'ava.realtime',
      );
      state = state.copyWith(connectionHealth: AvaConnectionHealth.connected);
      if (!state.userMicIntent) {
        state = state.copyWith(
          turnState: AvaSessionPhase.idle,
          clearError: true,
        );
        _scheduleWarmExpiry(runtime);
      }
      unawaited(_watchRuntime(runtime, runFuture, epoch));
    } catch (error, stackTrace) {
      if (runtime != null) await runtime.close();
      if (_isStale(epoch)) return;
      if (identical(_runtime, runtime)) {
        _runtime = null;
        _runFuture = null;
      }
      state = state.copyWith(
        turnState: AvaSessionPhase.failure,
        userMicIntent: false,
        actualTrackEnabled: false,
        connectionHealth: AvaConnectionHealth.failed,
        errorMessage: _messageFor(error),
      );
      Error.throwWithStackTrace(error, stackTrace);
    }
  }

  Future<void> _watchRuntime(
    AvaRealtimeTurnRuntime runtime,
    Future<void> runFuture,
    int epoch,
  ) async {
    Object? failure;
    StackTrace? failureStack;
    try {
      await runFuture;
      if (!_isStale(epoch)) {
        failure = const AvaRealtimeRuntimeException(
          'Ava conversation connection ended unexpectedly.',
        );
        failureStack = StackTrace.current;
      }
    } catch (error, stackTrace) {
      failure = error;
      failureStack = stackTrace;
    }
    if (_isStale(epoch) || !identical(_runtime, runtime)) return;
    await _failConnection(
      runtime: runtime,
      epoch: epoch,
      error: failure ??
          const AvaRealtimeRuntimeException(
            'Ava conversation connection ended unexpectedly.',
          ),
      stackTrace: failureStack ?? StackTrace.current,
    );
  }

  Future<void> _failConnection({
    required AvaRealtimeTurnRuntime runtime,
    required int epoch,
    required Object error,
    required StackTrace stackTrace,
  }) async {
    if (_isStale(epoch) || !identical(_runtime, runtime)) return;
    _connectionEpoch += 1;
    _intentRevision += 1;
    _warmExpiry?.cancel();
    _warmExpiry = null;
    _runtime = null;
    _runFuture = null;
    Object visibleError = error;
    try {
      await runtime.close();
    } catch (cleanupError, cleanupStackTrace) {
      visibleError = AvaRealtimeRuntimeException(
        '$error; conversation cleanup also failed: $cleanupError',
      );
      developer.log(
        'conversation_cleanup_failed error=$cleanupError',
        name: 'ava.realtime',
        error: cleanupError,
        stackTrace: cleanupStackTrace,
      );
    }
    if (_disposed) return;
    developer.log(
      'conversation_failed error=$visibleError',
      name: 'ava.realtime',
      error: visibleError,
      stackTrace: stackTrace,
    );
    if (kDebugMode) {
      debugPrint(
        'AVA_CONVERSATION_FAILURE epoch=$epoch '
        'mic_intent=${state.userMicIntent} '
        'track_enabled=${state.actualTrackEnabled} error=$visibleError',
      );
    }
    state = state.copyWith(
      turnState: AvaSessionPhase.failure,
      userMicIntent: false,
      actualTrackEnabled: false,
      connectionHealth: AvaConnectionHealth.failed,
      errorMessage: _messageFor(visibleError),
    );
  }

  void _scheduleWarmExpiry(AvaRealtimeTurnRuntime runtime) {
    _warmExpiry?.cancel();
    _warmExpiry = Timer(_warmRetention, () {
      if (_disposed || state.userMicIntent || !identical(_runtime, runtime)) {
        return;
      }
      unawaited(_expireWarmConnection(runtime));
    });
  }

  Future<void> _expireWarmConnection(AvaRealtimeTurnRuntime runtime) async {
    try {
      await disconnect(reason: 'mutedConversationExpired');
    } catch (error, stackTrace) {
      if (_disposed) return;
      Object visibleError = error;
      try {
        await runtime.close();
      } catch (cleanupError) {
        visibleError = AvaRealtimeRuntimeException(
          '$error; expired conversation cleanup also failed: $cleanupError',
        );
      }
      developer.log(
        'conversation_expiry_failed error=$visibleError',
        name: 'ava.realtime',
        error: visibleError,
        stackTrace: stackTrace,
      );
      state = state.copyWith(
        turnState: AvaSessionPhase.failure,
        userMicIntent: false,
        actualTrackEnabled: false,
        connectionHealth: AvaConnectionHealth.failed,
        errorMessage: _messageFor(visibleError),
      );
    }
  }

  /// Releases the conversation for reset, expiry, lifecycle shutdown or an
  /// explicit fatal recovery. The microphone button never calls this method.
  Future<void> disconnect({String reason = 'conversationDisconnected'}) async {
    _requireActiveController();
    _intentRevision += 1;
    _connectionEpoch += 1;
    _warmExpiry?.cancel();
    _warmExpiry = null;
    state = state.copyWith(
      turnState: AvaSessionPhase.idle,
      userMicIntent: false,
      actualTrackEnabled: false,
      connectionHealth: AvaConnectionHealth.disconnected,
      clearTurn: true,
      clearActiveSegment: true,
      clearError: true,
    );

    final runtime = _runtime;
    final runFuture = _runFuture;
    final preparation = _preparing;
    _runtime = null;
    _runFuture = null;
    if (runtime != null) {
      try {
        await runtime.stopConversation(reason: reason);
        if (runFuture != null) await runFuture;
      } catch (error, stackTrace) {
        try {
          await runtime.close();
        } catch (cleanupError) {
          Error.throwWithStackTrace(
            AvaRealtimeRuntimeException(
              '$error; conversation cleanup also failed: $cleanupError',
            ),
            stackTrace,
          );
        }
        Error.throwWithStackTrace(error, stackTrace);
      }
    }
    if (preparation != null) await preparation;
  }

  Future<bool> reportCanvasInteraction(
    AvaCanvasInteractionOutcome outcome,
  ) async {
    final runtime = _runtime;
    if (runtime == null) {
      if (outcome.action == 'explore' ||
          outcome.action == 'select' ||
          outcome.action == 'submitOutcome') {
        throw StateError(
          'A semantic canvas interaction requires an active Ava session.',
        );
      }
      return false;
    }
    return runtime.reportCanvasInteraction(outcome);
  }

  void clearError() {
    if (state.errorMessage == null) return;
    state = state.copyWith(
      turnState: state.connectionHealth == AvaConnectionHealth.connected &&
              state.userMicIntent
          ? AvaSessionPhase.listening
          : AvaSessionPhase.idle,
      clearError: true,
    );
  }

  void clearMemoryWarning() {
    if (state.memoryWarning == null) return;
    state = state.copyWith(clearMemoryWarning: true);
  }

  void _handlePresentationChanged() {
    if (_disposed) return;
    final presentation = _presentation.state;
    final microphoneIntent = state.userMicIntent;
    final resolvedPhase = switch (presentation.phase) {
      AvaRealtimeTurnPhase.created ||
      AvaRealtimeTurnPhase.connecting ||
      AvaRealtimeTurnPhase.listening =>
        microphoneIntent ? AvaSessionPhase.listening : AvaSessionPhase.idle,
      AvaRealtimeTurnPhase.planning ||
      AvaRealtimeTurnPhase.synthesizing ||
      AvaRealtimeTurnPhase.cancelling =>
        AvaSessionPhase.thinking,
      AvaRealtimeTurnPhase.speaking => AvaSessionPhase.speaking,
      AvaRealtimeTurnPhase.completed ||
      AvaRealtimeTurnPhase.cancelled =>
        microphoneIntent ? AvaSessionPhase.listening : AvaSessionPhase.idle,
      AvaRealtimeTurnPhase.failed => AvaSessionPhase.failure,
      null => state.turnState,
    };
    state = state.copyWith(
      turnState: resolvedPhase,
      turnId: presentation.turnId,
      activeSegmentId: presentation.currentSegment?.id,
      errorMessage: presentation.failureMessage,
      clearError: presentation.failureMessage == null,
      canvasState: presentation.canvasRejected
          ? AvaCanvasSessionState.rejected
          : presentation.phase == AvaRealtimeTurnPhase.created
              ? _canvasEngine.panelVisible
                  ? AvaCanvasSessionState.visible
                  : AvaCanvasSessionState.hidden
              : state.canvasState,
      memoryState: presentation.memoryWarning == null
          ? state.memoryState
          : AvaMemorySessionState.retryRequired,
      memoryWarning: presentation.memoryWarning,
    );

    if (!presentation.isActive &&
        presentation.turnId != null &&
        presentation.revision != _lastLoggedTerminalRevision) {
      _lastLoggedTerminalRevision = presentation.revision;
      developer.log(
        'turn_terminal session=${presentation.sessionId} '
        'turn=${presentation.turnId} phase=${presentation.phase?.name} '
        'failure_code=${presentation.failureCode} '
        'failure_id=${presentation.failureId} '
        'failure_component=${presentation.failureComponent} '
        'canvas_state=${state.canvasState.name} '
        'memory_state=${state.memoryState.name} '
        'track_enabled=${state.actualTrackEnabled} '
        'mic_intent=${state.userMicIntent}',
        name: 'ava.realtime',
      );
    }
  }

  void _handleMicrophoneTrackChanged(int epoch, bool enabled) {
    if (_isStale(epoch)) return;
    state = state.copyWith(actualTrackEnabled: enabled);
  }

  void _handleCanvasChanged() {
    if (_disposed) return;
    state = state.copyWith(
      canvasState: _canvasEngine.panelVisible
          ? AvaCanvasSessionState.visible
          : state.canvasState == AvaCanvasSessionState.rejected
              ? AvaCanvasSessionState.rejected
              : AvaCanvasSessionState.hidden,
    );
  }

  bool _isStale(int epoch) => _disposed || epoch != _connectionEpoch;

  void _requireActiveController() {
    if (_disposed) {
      throw StateError('Ava realtime session controller is disposed.');
    }
  }

  String _messageFor(Object error) {
    if (error is AvaRealtimeRuntimeException) return error.message;
    if (error is StateError) return error.message;
    if (error is FormatException) return error.message.toString();
    return 'Ava could not complete the conversation: $error';
  }

  @override
  void dispose() {
    _disposed = true;
    _intentRevision += 1;
    _connectionEpoch += 1;
    _warmExpiry?.cancel();
    _warmExpiry = null;
    _presentation.removeListener(_handlePresentationChanged);
    _canvasEngine.removeListener(_handleCanvasChanged);
    final runtime = _runtime;
    _runtime = null;
    _runFuture = null;
    if (runtime != null) unawaited(runtime.close());
    super.dispose();
  }
}
