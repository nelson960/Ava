import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/foundation.dart';

import '../canvas/ava_canvas_engine.dart';
import '../models/ava_canvas_protocol.dart';
import '../models/ava_realtime_protocol.dart';
import 'ava_realtime_presentation.dart';
import 'ava_realtime_session.dart';
import 'ava_realtime_trace.dart';
import 'ava_realtime_webrtc.dart';
import 'pcm_speech_output_service.dart';

final class AvaRealtimeRuntimeException implements Exception {
  const AvaRealtimeRuntimeException(this.message);

  final String message;

  @override
  String toString() => 'AvaRealtimeRuntimeException: $message';
}

/// Owns one protocol-v3 conversation from ordered frames through native PCM.
///
/// This class deliberately has no provider SDK dependency. A live WebSocket
/// Consecutive turns retain the transport while resetting all turn-local
/// validation, playback, caption, viseme, and canvas-cue state.
final class AvaRealtimeTurnRuntime {
  AvaRealtimeTurnRuntime({
    required String sessionId,
    required AvaSessionLimitsV3 limits,
    required AvaRealtimeMediaSession mediaSession,
    required AvaRealtimeTransport transport,
    required PcmSpeechOutputService speechOutput,
    required AvaCanvasEngine canvasEngine,
    required AvaRealtimePresentationController presentation,
    required double volume,
    required String Function() cancelRequestIdFactory,
    AvaRealtimeTrace? trace,
    void Function(bool enabled)? onMicrophoneTrackChanged,
    bool automaticPlaybackPolling = true,
    bool microphoneInitiallyEnabled = true,
  })  : _sessionId = sessionId,
        _limits = limits,
        _mediaSession = mediaSession,
        _transport = transport,
        _speechOutput = speechOutput,
        _canvasEngine = canvasEngine,
        _presentation = presentation,
        _volume = volume,
        _cancelRequestIdFactory = cancelRequestIdFactory,
        _trace = trace,
        _onMicrophoneTrackChanged = onMicrophoneTrackChanged,
        _automaticPlaybackPolling = automaticPlaybackPolling,
        _microphoneRequested = microphoneInitiallyEnabled {
    if (volume < 0 || volume > 1) {
      throw RangeError.range(volume, 0, 1, 'volume');
    }
    if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(sessionId)) {
      throw ArgumentError.value(
          sessionId, 'sessionId', 'has an invalid format');
    }
  }

  final String _sessionId;
  final AvaSessionLimitsV3 _limits;
  final AvaRealtimeMediaSession _mediaSession;
  final AvaRealtimeTransport _transport;
  final PcmSpeechOutputService _speechOutput;
  final AvaCanvasEngine _canvasEngine;
  final AvaRealtimePresentationController _presentation;
  final double _volume;
  final String Function() _cancelRequestIdFactory;
  final AvaRealtimeTrace? _trace;
  final void Function(bool enabled)? _onMicrophoneTrackChanged;
  final bool _automaticPlaybackPolling;
  AvaRealtimeTurnStateMachine _machine = AvaRealtimeTurnStateMachine();
  final List<_PendingCanvasCue> _canvasCues = [];
  final Set<String> _appliedCanvasCueIds = {};

  Future<void> _sendTail = Future<void>.value();
  Timer? _progressTimer;
  AvaRealtimeServerEvent? _pendingAudioDescriptor;
  String? _turnId;
  String? _streamId;
  PcmPlaybackProgress? _lastProgress;
  int? _finalRenderedSampleCount;
  int _nextClientSequence = 0;
  bool _started = false;
  bool _closed = false;
  bool _terminal = false;
  bool _playbackStarted = false;
  bool _playbackFinishStarted = false;
  bool _pollInFlight = false;
  bool _interrupting = false;
  bool _microphoneRequested;
  Object? _asynchronousFailure;
  StackTrace? _asynchronousFailureStack;
  Completer<void> _turnTerminal = Completer<void>();
  final Completer<void> _firstListening = Completer<void>();
  bool _firstSegmentTraced = false;
  bool _firstPcmTraced = false;

  AvaRealtimeTurnSnapshot get snapshot => _machine.snapshot;
  bool get isTerminal => _terminal;
  bool get microphoneTrackEnabled => _mediaSession.microphoneEnabled;
  Future<void> get firstListening => _firstListening.future;

  /// Reports only interactions whose meaning belongs in conversation context.
  ///
  /// Reveal, toggle, drag, state, and reset actions remain instant and local.
  /// Explore, select, and submitOutcome are sent as IDs only; the backend
  /// resolves their meaning from its canonical validated binding.
  Future<bool> reportCanvasInteraction(
    AvaCanvasInteractionOutcome outcome,
  ) async {
    if (outcome.action != 'explore' &&
        outcome.action != 'select' &&
        outcome.action != 'submitOutcome') {
      return false;
    }
    if (!_started || _closed) {
      throw StateError(
        'Cannot report a semantic canvas interaction outside an active '
        'conversation session.',
      );
    }
    await _sendClient(
      (sequence) => AvaRealtimeClientEvent.canvasInteraction(
        sessionId: _sessionId,
        sequence: sequence,
        sceneId: outcome.sceneId,
        sceneRevision: outcome.sceneRevision,
        interactionRevision: outcome.interactionRevision,
        bindingId: outcome.bindingId,
      ),
    );
    return true;
  }

  Future<void> run() async {
    if (_started) {
      throw StateError('Ava realtime runtime may be started only once.');
    }
    if (_closed) {
      throw StateError('Ava realtime runtime is closed.');
    }
    _started = true;

    try {
      developer.log(
        'runtime_started session=$_sessionId',
        name: 'ava.realtime',
      );
      _trace?.mark('runtime.started');
      await _sendClient(
        (sequence) => AvaRealtimeClientEvent.clientReady(
          sessionId: _sessionId,
          sequence: sequence,
        ),
      );
      _trace?.mark('client.ready');
      await for (final frame in _transport.frames()) {
        _throwAsynchronousFailure();
        if (_pendingAudioDescriptor != null && frame is! AvaRealtimePcmFrame) {
          throw const AvaRealtimeRuntimeException(
            'audioChunk must be followed immediately by one binary PCM frame.',
          );
        }
        switch (frame) {
          case AvaRealtimeControlFrame():
            await _handleControl(frame.event);
            break;
          case AvaRealtimePcmFrame():
            await _handlePcm(frame.bytes);
            break;
        }
      }
      if (_closed) return;
      _throwAsynchronousFailure();
      if (_pendingAudioDescriptor != null) {
        throw const AvaRealtimeRuntimeException(
          'Realtime transport closed before the pending PCM frame arrived.',
        );
      }
      if (!_terminal) {
        throw const AvaRealtimeRuntimeException(
          'Realtime transport closed before a terminal turn event.',
        );
      }
      if (snapshot.phase == AvaRealtimeTurnPhase.failed) {
        throw AvaRealtimeRuntimeException(
          _presentation.state.failureMessage ??
              'Ava ended the connection after a failed turn.',
        );
      }
      throw const AvaRealtimeRuntimeException(
        'Realtime transport closed after a turn instead of remaining '
        'available for the conversation.',
      );
    } catch (error, stackTrace) {
      developer.log(
        'runtime_failed session=$_sessionId turn=$_turnId '
        'phase=${snapshot.phase?.name} error=$error',
        name: 'ava.realtime',
        error: error,
        stackTrace: stackTrace,
      );
      if (kDebugMode) {
        debugPrint(
          'AVA_REALTIME_FAILURE session=$_sessionId turn=$_turnId '
          'phase=${snapshot.phase?.name} error=$error',
        );
      }
      _progressTimer?.cancel();
      _progressTimer = null;
      if (!_terminal) {
        _presentation.failLocally(error);
      }
      Object? microphoneError;
      try {
        await _setMicrophoneEnabled(false);
      } catch (error) {
        microphoneError = error;
      }
      Object? cleanupError;
      if (_playbackStarted &&
          _finalRenderedSampleCount == null &&
          !_interrupting) {
        try {
          await _speechOutput.stop();
        } catch (error) {
          cleanupError = error;
        }
      }
      if (cleanupError != null || microphoneError != null) {
        Error.throwWithStackTrace(
          AvaRealtimeRuntimeException(
            '$error; cleanup also failed: '
            'microphone=$microphoneError PCM=$cleanupError',
          ),
          stackTrace,
        );
      }
      Error.throwWithStackTrace(error, stackTrace);
    }
  }

  /// Polls the native rendered-sample clock once.
  ///
  /// Tests call this explicitly. Live sessions also call it from a bounded
  /// local timer configured by the server bootstrap.
  Future<void> synchronizePlayback() async {
    if (!_playbackStarted ||
        _terminal ||
        _interrupting ||
        _finalRenderedSampleCount != null ||
        _pollInFlight) {
      return;
    }
    _pollInFlight = true;
    try {
      final turnId = _requireTurnId();
      final progress = await _speechOutput.progress(turnId: turnId);
      // The final native buffer can complete while this platform-channel read
      // is in flight. The finish callback owns publication of that terminal
      // sample count, so a crossed timer read must not publish it twice.
      if (_terminal || _interrupting || _finalRenderedSampleCount != null) {
        return;
      }
      if (progress.sampleRateHz != 24000) {
        throw const AvaRealtimeRuntimeException(
          'Native PCM progress must use the protocol 24 kHz sample clock.',
        );
      }
      if (progress.scheduledSampleCount > snapshot.receivedSampleCount) {
        throw const AvaRealtimeRuntimeException(
          'Native PCM scheduled samples exceed validated received PCM.',
        );
      }
      // Only the native finish callback may publish the terminal playback
      // position. A polling tick can observe the final sample just before that
      // callback runs; sending both would complete the backend turn and then
      // violate its terminal state with a duplicate progress event.
      if (progress.renderedSampleCount == snapshot.receivedSampleCount &&
          progress.bufferedSampleCount == 0) {
        return;
      }
      await _publishProgress(progress);
    } finally {
      _pollInFlight = false;
    }
  }

  Future<void> interrupt({
    String reason = 'childStartedSpeaking',
  }) async {
    if (!_started || _terminal) {
      throw StateError('Cannot interrupt an inactive realtime turn.');
    }
    if (_interrupting) return;
    final normalizedReason = reason.trim();
    if (normalizedReason.isEmpty || normalizedReason.runes.length > 120) {
      throw ArgumentError.value(
        reason,
        'reason',
        'must contain 1-120 characters',
      );
    }
    final requestId = _cancelRequestIdFactory();
    if (!_machine.registerCancellation(requestId)) return;
    _interrupting = true;
    _progressTimer?.cancel();
    _progressTimer = null;

    final turnId = _requireTurnId();
    if (_playbackStarted && _streamId != null) {
      final stopped = await _speechOutput.fadeOutAndStop(turnId: turnId);
      _finalRenderedSampleCount = stopped.renderedSampleCount;
      _presentation.updatePlaybackClock(
        renderedSampleCount: stopped.renderedSampleCount,
        receivedSampleCount: snapshot.receivedSampleCount,
      );
      await _applyDueCanvasCues(stopped.renderedSampleCount);
      await _sendClient(
        (sequence) => AvaRealtimeClientEvent.playbackStopped(
          sessionId: _sessionId,
          sequence: sequence,
          turnId: turnId,
          streamId: _streamId!,
          renderedSampleCount: stopped.renderedSampleCount,
          reason: normalizedReason,
        ),
      );
    }
    await _sendClient(
      (sequence) => AvaRealtimeClientEvent.cancelTurn(
        sessionId: _sessionId,
        sequence: sequence,
        turnId: turnId,
        requestId: requestId,
        reason: normalizedReason,
      ),
    );
  }

  /// Mutes the existing WebRTC audio track without tearing down the call.
  ///
  /// Pausing while Ava is speaking first commits the delivered audio prefix
  /// through the normal cancellation contract. Pausing while she is already
  /// listening only mutes the track, so a later resume does not repeat
  /// bootstrap, SDP, ICE, or sideband negotiation.
  Future<void> pauseConversation({
    String reason = 'userPaused',
  }) async {
    if (!_started || _closed) {
      throw StateError('Cannot pause an inactive realtime conversation.');
    }
    _microphoneRequested = false;
    await _setMicrophoneEnabled(false);

    if (_terminal || _turnId == null) return;
    if (snapshot.phase == AvaRealtimeTurnPhase.listening || _interrupting) {
      return;
    }
    await interrupt(reason: reason);
    await _turnTerminal.future.timeout(
      Duration(seconds: _limits.maxTurnSeconds),
      onTimeout: () => throw const AvaRealtimeRuntimeException(
        'Ava did not acknowledge conversation pause before the negotiated '
        'turn deadline.',
      ),
    );
  }

  /// Re-enables the already-negotiated microphone track.
  Future<void> resumeConversation() async {
    if (!_started || _closed) {
      throw StateError('Cannot resume an inactive realtime conversation.');
    }
    _microphoneRequested = true;
    if (!_terminal &&
        _turnId != null &&
        snapshot.phase == AvaRealtimeTurnPhase.listening) {
      await _setMicrophoneEnabled(true);
    }
  }

  /// Cancels the active turn, waits for its authoritative delivered-prefix
  /// terminal event, and then closes the persistent conversation transport.
  Future<void> stopConversation({
    String reason = 'userStopped',
  }) async {
    if (!_started || _closed) {
      await close();
      return;
    }
    if (!_terminal && _turnId != null) {
      await interrupt(reason: reason);
      await _turnTerminal.future.timeout(
        Duration(seconds: _limits.maxTurnSeconds),
        onTimeout: () => throw const AvaRealtimeRuntimeException(
          'Ava did not acknowledge conversation cancellation before the '
          'negotiated turn deadline.',
        ),
      );
    }
    await close();
  }

  Future<void> close() async {
    if (_closed) return;
    _closed = true;
    _progressTimer?.cancel();
    _progressTimer = null;
    Object? stopError;
    if (_playbackStarted &&
        !_terminal &&
        _finalRenderedSampleCount == null &&
        !_interrupting) {
      try {
        await _speechOutput.stop();
      } catch (error) {
        stopError = error;
      }
    }
    Object? transportError;
    try {
      await _transport.close();
    } catch (error) {
      transportError = error;
    }
    Object? mediaError;
    try {
      await _mediaSession.close();
    } catch (error) {
      mediaError = error;
    }
    if (stopError != null) {
      throw AvaRealtimeRuntimeException(
        'Closing realtime PCM playback failed: $stopError',
      );
    }
    if (transportError != null || mediaError != null) {
      throw AvaRealtimeRuntimeException(
        'Closing realtime transport failed: '
        'event=$transportError media=$mediaError',
      );
    }
  }

  Future<void> _handleControl(AvaRealtimeServerEvent event) async {
    if (event.sessionId != _sessionId) {
      throw const AvaRealtimeRuntimeException(
        'Server event sessionId does not match session bootstrap.',
      );
    }
    if (event.type == AvaRealtimeServerEventType.turnCreated && _terminal) {
      _resetForNextTurn();
    }
    _machine.accept(event);

    switch (event.type) {
      case AvaRealtimeServerEventType.turnCreated:
        _turnId = event.turnId;
        _trace?.mark('turn.created');
        _presentation.beginTurn(
          sessionId: event.sessionId,
          turnId: event.turnId,
        );
        break;
      case AvaRealtimeServerEventType.turnPhaseChanged:
        developer.log(
          'turn_phase session=$_sessionId phase=${event.phase!.name}',
          name: 'ava.realtime',
        );
        _presentation.setPhase(event.phase!);
        if (event.phase == AvaRealtimeTurnPhase.listening) {
          _trace?.mark('turn.listening');
          developer.log(
            'microphone_enable_requested session=$_sessionId',
            name: 'ava.realtime',
          );
          await _setMicrophoneEnabled(_microphoneRequested);
          _trace?.mark('microphone.enabled');
          if (!_firstListening.isCompleted) {
            _firstListening.complete();
          }
          developer.log(
            'microphone_enable_completed session=$_sessionId',
            name: 'ava.realtime',
          );
        }
        if (event.phase == AvaRealtimeTurnPhase.speaking) {
          _startPlaybackFinish();
        }
        break;
      case AvaRealtimeServerEventType.segmentReady:
        if (!_firstSegmentTraced) {
          _firstSegmentTraced = true;
          _trace?.mark('response.first_segment');
        }
        _presentation.addSegment(event.segmentIndex!, event.segment!);
        break;
      case AvaRealtimeServerEventType.audioMetadata:
        await _startPlayback(event);
        break;
      case AvaRealtimeServerEventType.alignmentBatch:
        _presentation.addAlignmentSpans(event.alignmentSpans);
        break;
      case AvaRealtimeServerEventType.audioChunk:
        if (event.byteLength! > _limits.maxAudioChunkBytes) {
          throw AvaRealtimeBufferOverflow(
            'PCM descriptor exceeds the negotiated '
            '${_limits.maxAudioChunkBytes}-byte limit.',
          );
        }
        _pendingAudioDescriptor = event;
        break;
      case AvaRealtimeServerEventType.canvasCue:
        developer.log(
          'canvas_cue_received session=$_sessionId turn=${event.turnId} '
          'cue=${event.cueId} scene=${event.transaction!.sceneId} '
          'revision=${event.transaction!.revision} '
          'sample_offset=${event.sampleOffset}',
          name: 'ava.realtime.canvas',
        );
        _canvasCues.add(
          _PendingCanvasCue(
            cueId: event.cueId!,
            sampleOffset: event.sampleOffset!,
            transaction: event.transaction!,
          ),
        );
        _canvasCues.sort(
          (left, right) => left.sampleOffset.compareTo(right.sampleOffset),
        );
        await _applyDueCanvasCues(
          _lastProgress?.renderedSampleCount ?? 0,
        );
        break;
      case AvaRealtimeServerEventType.canvasRejected:
        final failure = event.failure!;
        _presentation.recordCanvasRejection(
          failure.message,
          failureId: failure.failureId,
          component: failure.component,
        );
        break;
      case AvaRealtimeServerEventType.interactionRejected:
        _presentation.recordCanvasInteractionRejection(
          event.interactionRejectionMessage!,
        );
        break;
      case AvaRealtimeServerEventType.memoryPersistenceFailed:
        _presentation.recordMemoryPersistenceFailure(event.failure!);
        break;
      case AvaRealtimeServerEventType.turnCompleted:
      case AvaRealtimeServerEventType.turnCancelled:
      case AvaRealtimeServerEventType.turnFailed:
        await _handleTerminal(event);
        break;
    }
  }

  Future<void> _startPlayback(AvaRealtimeServerEvent event) async {
    if (_playbackStarted) {
      throw const AvaRealtimeRuntimeException(
        'Native PCM playback may start only once per turn.',
      );
    }
    _streamId = event.streamId;
    await _speechOutput.start(
      turnId: _requireTurnId(),
      sampleRateHz: 24000,
      volume: _volume,
    );
    _playbackStarted = true;
    await _sendClient(
      (sequence) => AvaRealtimeClientEvent.playbackStarted(
        sessionId: _sessionId,
        sequence: sequence,
        turnId: _requireTurnId(),
        streamId: _streamId!,
      ),
    );
    if (_automaticPlaybackPolling) {
      _progressTimer = Timer.periodic(
        Duration(
          milliseconds: _limits.playbackProgressIntervalMs,
        ),
        (_) {
          unawaited(
            synchronizePlayback().catchError(
              (Object error, StackTrace stackTrace) {
                _recordAsynchronousFailure(error, stackTrace);
              },
            ),
          );
        },
      );
    }
  }

  Future<void> _handlePcm(Uint8List bytes) async {
    final descriptor = _pendingAudioDescriptor;
    if (descriptor == null) {
      throw const AvaRealtimeRuntimeException(
        'Binary PCM arrived without an audioChunk descriptor.',
      );
    }
    if (bytes.length != descriptor.byteLength ||
        bytes.length != descriptor.sampleCount! * 2) {
      throw AvaRealtimeRuntimeException(
        'Binary PCM length ${bytes.length} does not match descriptor '
        '${descriptor.byteLength}.',
      );
    }
    _pendingAudioDescriptor = null;
    if (!_firstPcmTraced) {
      _firstPcmTraced = true;
      _trace?.mark('audio.first_pcm');
    }
    await _speechOutput.append(
      turnId: _requireTurnId(),
      audio: bytes,
    );
    _presentation.updatePlaybackClock(
      renderedSampleCount: _lastProgress?.renderedSampleCount ?? 0,
      receivedSampleCount: snapshot.receivedSampleCount,
    );
  }

  void _startPlaybackFinish() {
    if (_playbackFinishStarted) {
      throw const AvaRealtimeRuntimeException(
        'Native PCM finish may start only once per turn.',
      );
    }
    if (!_playbackStarted || snapshot.receivedSampleCount == 0) {
      throw const AvaRealtimeRuntimeException(
        'Speaking requires non-empty native PCM playback.',
      );
    }
    _playbackFinishStarted = true;
    unawaited(
      _speechOutput
          .finish(turnId: _requireTurnId())
          .then(_handlePlaybackFinished)
          .catchError((Object error, StackTrace stackTrace) {
        if (_interrupting) return;
        _recordAsynchronousFailure(error, stackTrace);
      }),
    );
  }

  Future<void> _handlePlaybackFinished(PcmStopResult result) async {
    if (_interrupting || _terminal) return;
    if (result.renderedSampleCount != snapshot.receivedSampleCount) {
      throw AvaRealtimeRuntimeException(
        'Native PCM completed ${result.renderedSampleCount} samples, '
        'but protocol received ${snapshot.receivedSampleCount}.',
      );
    }
    _finalRenderedSampleCount = result.renderedSampleCount;
    _progressTimer?.cancel();
    _progressTimer = null;
    await _publishProgress(
      PcmPlaybackProgress(
        renderedSampleCount: result.renderedSampleCount,
        scheduledSampleCount: result.renderedSampleCount,
        sampleRateHz: 24000,
      ),
    );
  }

  Future<void> _publishProgress(PcmPlaybackProgress progress) async {
    if (progress.renderedSampleCount > snapshot.receivedSampleCount) {
      throw const AvaRealtimeRuntimeException(
        'Rendered sample count exceeds validated received PCM.',
      );
    }
    _presentation.updatePlaybackClock(
      renderedSampleCount: progress.renderedSampleCount,
      receivedSampleCount: snapshot.receivedSampleCount,
    );
    await _applyDueCanvasCues(progress.renderedSampleCount);
    final previous = _lastProgress;
    _lastProgress = progress;
    if (previous?.renderedSampleCount == progress.renderedSampleCount &&
        previous?.bufferedSampleCount == progress.bufferedSampleCount) {
      return;
    }
    await _sendClient(
      (sequence) => AvaRealtimeClientEvent.playbackProgress(
        sessionId: _sessionId,
        sequence: sequence,
        turnId: _requireTurnId(),
        streamId: _requireStreamId(),
        renderedSampleCount: progress.renderedSampleCount,
        bufferedSampleCount: progress.bufferedSampleCount,
      ),
    );
  }

  Future<void> _applyDueCanvasCues(int renderedSampleCount) async {
    for (final cue in _canvasCues) {
      if (cue.sampleOffset > renderedSampleCount) break;
      if (!_appliedCanvasCueIds.add(cue.cueId)) continue;
      final result = _canvasEngine.apply(cue.transaction);
      if (!result.accepted) {
        final rejection = result.rejection!;
        developer.log(
          'canvas_transaction_rejected session=$_sessionId '
          'turn=${_requireTurnId()} cue=${cue.cueId} '
          'scene=${cue.transaction.sceneId} '
          'revision=${cue.transaction.revision} '
          'code=${rejection.code.name} message=${rejection.message}',
          name: 'ava.realtime.canvas',
          error: rejection,
        );
        _presentation.recordCanvasRejection(
          'Canvas cue ${cue.cueId} was rejected '
          '(${rejection.code.name}): ${rejection.message}',
        );
      } else {
        developer.log(
          'canvas_transaction_applied session=$_sessionId '
          'turn=${_requireTurnId()} cue=${cue.cueId} '
          'scene=${cue.transaction.sceneId} '
          'revision=${cue.transaction.revision} '
          'bindings=${cue.transaction.bindings.length}',
          name: 'ava.realtime.canvas',
        );
        _presentation.recordCanvasApplied();
      }
    }
  }

  Future<void> _handleTerminal(AvaRealtimeServerEvent event) async {
    if (_pendingAudioDescriptor != null) {
      throw const AvaRealtimeRuntimeException(
        'Terminal event arrived before pending binary PCM.',
      );
    }
    final delivered = event.deliveredSampleCount!;
    final localRendered =
        _finalRenderedSampleCount ?? _lastProgress?.renderedSampleCount ?? 0;
    switch (event.type) {
      case AvaRealtimeServerEventType.turnCompleted:
        if (delivered != localRendered ||
            delivered != snapshot.receivedSampleCount) {
          throw AvaRealtimeRuntimeException(
            'Completed delivered samples $delivered do not match local '
            'rendered samples $localRendered.',
          );
        }
        await _applyDueCanvasCues(delivered);
        break;
      case AvaRealtimeServerEventType.turnCancelled:
        if (!_interrupting || delivered != localRendered) {
          throw AvaRealtimeRuntimeException(
            'Cancelled delivered samples $delivered do not match the local '
            'stop position $localRendered.',
          );
        }
        break;
      case AvaRealtimeServerEventType.turnFailed:
        if (delivered > localRendered) {
          throw AvaRealtimeRuntimeException(
            'Failed turn delivered samples $delivered exceed local rendered '
            'samples $localRendered.',
          );
        }
        if (_playbackStarted &&
            _finalRenderedSampleCount == null &&
            !_interrupting) {
          await _speechOutput.stop();
        }
        break;
      default:
        throw StateError('Unexpected non-terminal event.');
    }
    _progressTimer?.cancel();
    _progressTimer = null;
    _terminal = true;
    _presentation.complete(event);
    if (!_turnTerminal.isCompleted) _turnTerminal.complete();
  }

  void _resetForNextTurn() {
    _progressTimer?.cancel();
    _progressTimer = null;
    _machine = AvaRealtimeTurnStateMachine();
    _canvasCues.clear();
    _appliedCanvasCueIds.clear();
    _pendingAudioDescriptor = null;
    _turnId = null;
    _streamId = null;
    _lastProgress = null;
    _finalRenderedSampleCount = null;
    _terminal = false;
    _playbackStarted = false;
    _playbackFinishStarted = false;
    _pollInFlight = false;
    _interrupting = false;
    _asynchronousFailure = null;
    _asynchronousFailureStack = null;
    _turnTerminal = Completer<void>();
    _firstSegmentTraced = false;
    _firstPcmTraced = false;
  }

  Future<void> _setMicrophoneEnabled(bool enabled) async {
    await _mediaSession.setMicrophoneEnabled(enabled);
    final actual = _mediaSession.microphoneEnabled;
    if (actual != enabled) {
      throw AvaRealtimeRuntimeException(
        'Microphone track state did not match the requested state.',
      );
    }
    _onMicrophoneTrackChanged?.call(actual);
  }

  Future<void> _sendClient(
    AvaRealtimeClientEvent Function(int sequence) build,
  ) {
    final event = build(_nextClientSequence);
    _nextClientSequence += 1;
    final result = _sendTail.then((_) => _transport.send(event));
    _sendTail = result;
    return result;
  }

  void _recordAsynchronousFailure(Object error, StackTrace stackTrace) {
    if (_asynchronousFailure != null) return;
    _asynchronousFailure = error;
    _asynchronousFailureStack = stackTrace;
    _progressTimer?.cancel();
    _progressTimer = null;
    _presentation.failLocally(error);
    unawaited(_transport.close());
  }

  void _throwAsynchronousFailure() {
    final error = _asynchronousFailure;
    if (error == null) return;
    Error.throwWithStackTrace(
      error,
      _asynchronousFailureStack ?? StackTrace.current,
    );
  }

  String _requireTurnId() {
    final value = _turnId;
    if (value == null) {
      throw const AvaRealtimeRuntimeException(
        'Realtime turnId is not available yet.',
      );
    }
    return value;
  }

  String _requireStreamId() {
    final value = _streamId;
    if (value == null) {
      throw const AvaRealtimeRuntimeException(
        'Realtime audio streamId is not available yet.',
      );
    }
    return value;
  }
}

final class _PendingCanvasCue {
  const _PendingCanvasCue({
    required this.cueId,
    required this.sampleOffset,
    required this.transaction,
  });

  final String cueId;
  final int sampleOffset;
  final AvaCanvasTransaction transaction;
}
