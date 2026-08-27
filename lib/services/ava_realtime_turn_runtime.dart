import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/foundation.dart';

import '../canvas/ava_svg_canvas_controller.dart';
import '../canvas/ava_svg_canvas_telemetry.dart';
import '../models/ava_realtime_protocol.dart';
import '../models/ava_realtime_protocol_v4.dart';
import '../models/ava_svg_canvas_protocol.dart';
import 'ava_realtime_presentation.dart';
import 'ava_realtime_session_v4.dart';
import 'ava_realtime_trace.dart';
import 'ava_realtime_webrtc.dart';
import 'ava_svg_document_service.dart';
import 'pcm_speech_output_service.dart';

final class AvaRealtimeRuntimeException implements Exception {
  const AvaRealtimeRuntimeException(this.message);

  final String message;

  @override
  String toString() => 'AvaRealtimeRuntimeException: $message';
}

final class AvaRealtimeTurnSnapshotV4 {
  const AvaRealtimeTurnSnapshotV4({
    required this.sessionId,
    required this.turnId,
    required this.phase,
    required this.revision,
    required this.segmentCount,
    required this.receivedSampleCount,
    required this.terminal,
  });

  final String sessionId;
  final String? turnId;
  final AvaRealtimeTurnPhase? phase;
  final int revision;
  final int segmentCount;
  final int receivedSampleCount;
  final bool terminal;
}

/// Owns one persistent protocol-v4 conversation from raw frames through PCM.
///
/// This class deliberately has no provider SDK dependency. A live WebSocket
/// Consecutive turns retain the transport while resetting all turn-local
/// validation, playback, caption, and viseme state.
final class AvaRealtimeTurnRuntime {
  AvaRealtimeTurnRuntime({
    required String sessionId,
    required AvaSessionLimitsV4 limits,
    required AvaRealtimeMediaSession mediaSession,
    required AvaRealtimeRawTransportV4 transport,
    required AvaSvgDocumentLoader documents,
    required PcmSpeechOutputService speechOutput,
    required AvaRealtimePresentationController presentation,
    required double volume,
    required String Function() cancelRequestIdFactory,
    AvaSvgCanvasTelemetrySink? canvasTelemetry,
    AvaRetainedSceneRevisionLookup? retainedSceneRevision,
    void Function(AvaSvgCanvasController controller)? onCanvasClosed,
    AvaRealtimeTrace? trace,
    void Function(bool enabled)? onMicrophoneTrackChanged,
    bool automaticPlaybackPolling = true,
    bool microphoneInitiallyEnabled = true,
  })  : _sessionId = sessionId,
        _limits = limits,
        _mediaSession = mediaSession,
        _transport = transport,
        _speechOutput = speechOutput,
        _presentation = presentation,
        _volume = volume,
        _cancelRequestIdFactory = cancelRequestIdFactory,
        _canvasTelemetry = canvasTelemetry,
        _onCanvasClosed = onCanvasClosed,
        _trace = trace,
        _onMicrophoneTrackChanged = onMicrophoneTrackChanged,
        _automaticPlaybackPolling = automaticPlaybackPolling,
        _microphoneRequested = microphoneInitiallyEnabled {
    canvasController = AvaSvgCanvasController(
      documents: documents,
      commandSink: _enqueueCanvasCommand,
      telemetry: canvasTelemetry,
      retainedSceneRevision: retainedSceneRevision,
    );
    if (volume < 0 || volume > 1) {
      throw RangeError.range(volume, 0, 1, 'volume');
    }
    if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(sessionId)) {
      throw ArgumentError.value(
          sessionId, 'sessionId', 'has an invalid format');
    }
  }

  final String _sessionId;
  final AvaSessionLimitsV4 _limits;
  final AvaRealtimeMediaSession _mediaSession;
  final AvaRealtimeRawTransportV4 _transport;
  final PcmSpeechOutputService _speechOutput;
  final AvaRealtimePresentationController _presentation;
  final double _volume;
  final String Function() _cancelRequestIdFactory;
  final AvaSvgCanvasTelemetrySink? _canvasTelemetry;
  final void Function(AvaSvgCanvasController controller)? _onCanvasClosed;
  final AvaRealtimeTrace? _trace;
  final void Function(bool enabled)? _onMicrophoneTrackChanged;
  final bool _automaticPlaybackPolling;
  late final AvaSvgCanvasController canvasController;

  Future<void> _sendTail = Future<void>.value();
  Future<void> _microphoneTail = Future<void>.value();
  Timer? _progressTimer;
  AvaRealtimeServerEventV4? _pendingAudioDescriptor;
  String? _turnId;
  String? _streamId;
  AvaRealtimeTurnPhase? _phase;
  int _revision = -1;
  int _nextSegmentIndex = 0;
  int _nextAudioChunkIndex = 0;
  int _receivedSampleCount = 0;
  String? _validatedAudioStreamId;
  String? _cancelRequestId;
  PcmPlaybackProgress? _lastProgress;
  int? _finalRenderedSampleCount;
  int _nextServerSequence = 0;
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
  bool _firstRenderedSampleTraced = false;
  int _speechSegmentCount = 0;
  int _speechCharacterCount = 0;

  AvaRealtimeTurnSnapshotV4 get snapshot => AvaRealtimeTurnSnapshotV4(
        sessionId: _sessionId,
        turnId: _turnId,
        phase: _phase,
        revision: _revision,
        segmentCount: _nextSegmentIndex,
        receivedSampleCount: _receivedSampleCount,
        terminal: _terminal,
      );
  bool get isTerminal => _terminal;
  bool get microphoneTrackEnabled => _mediaSession.microphoneEnabled;
  Future<void> get firstListening => _firstListening.future;

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
        (sequence) => AvaRealtimeClientEventV4.clientReady(
          sessionId: _sessionId,
          sequence: sequence,
        ),
      );
      _trace?.mark('client.ready');
      await for (final frame in _transport.frames()) {
        if (_closed) return;
        _throwAsynchronousFailure();
        if (_pendingAudioDescriptor != null &&
            frame is! AvaRealtimePcmFrameV4) {
          throw const AvaRealtimeRuntimeException(
            'audioChunk must be followed immediately by one binary PCM frame.',
          );
        }
        switch (frame) {
          case AvaRealtimeControlFrameV4():
            _acceptServerSequence(frame.event);
            if (frame.event.isCanvas) {
              _dispatchCanvasEvent(frame.event);
            } else {
              await _handleControl(frame.event);
            }
            break;
          case AvaRealtimePcmFrameV4():
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
    if (!_registerCancellation(requestId)) return;
    _interrupting = true;
    _progressTimer?.cancel();
    _progressTimer = null;

    final turnId = _requireTurnId();
    if (_playbackStarted && _streamId != null) {
      final fadeStopwatch = Stopwatch()..start();
      final stopped = await _speechOutput.fadeOutAndStop(turnId: turnId);
      fadeStopwatch.stop();
      _finalRenderedSampleCount = stopped.renderedSampleCount;
      _traceFirstRenderedSample(stopped.renderedSampleCount);
      _trace?.mark(
        'speech.playback_interrupted',
        fields: _speechPlaybackFields(
          turnId: turnId,
          renderedSampleCount: stopped.renderedSampleCount,
          // The wire reason is intentionally extensible; do not copy that
          // caller-provided text into telemetry.
          termination: 'interrupted',
          extra: {
            'fadeOutAndStopDurationMs':
                fadeStopwatch.elapsedMicroseconds / 1000,
          },
        ),
      );
      _presentation.updatePlaybackClock(
        renderedSampleCount: stopped.renderedSampleCount,
        receivedSampleCount: snapshot.receivedSampleCount,
      );
      _updateCanvasPlaybackClock(
        turnId: turnId,
        renderedSampleCount: stopped.renderedSampleCount,
        event: 'playbackStopped',
      );
      await _sendClient(
        (sequence) => AvaRealtimeClientEventV4.playbackStopped(
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
      (sequence) => AvaRealtimeClientEventV4.cancelTurn(
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

  /// Sets whether the local child microphone track is active without
  /// interrupting Ava if she is currently speaking or planning.
  Future<void> setMicrophoneEnabled(bool enabled) async {
    if (!_started || _closed) return;
    _microphoneRequested = enabled;
    if (enabled) {
      if (!_terminal &&
          _turnId != null &&
          snapshot.phase == AvaRealtimeTurnPhase.listening) {
        await _setMicrophoneEnabled(true);
      }
    } else {
      await _setMicrophoneEnabled(false);
    }
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
    Object? microphoneError;
    try {
      await _microphoneTail;
    } catch (error) {
      microphoneError = error;
    }
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
    Object? sendError;
    try {
      await _sendTail;
    } catch (error) {
      sendError = error;
    }
    Object? transportError;
    try {
      await _transport.close();
    } catch (error) {
      transportError = error;
    }
    _onCanvasClosed?.call(canvasController);
    canvasController.dispose();
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
    if (microphoneError != null ||
        sendError != null ||
        transportError != null ||
        mediaError != null) {
      throw AvaRealtimeRuntimeException(
        'Closing realtime transport failed: '
        'microphone=$microphoneError send=$sendError '
        'event=$transportError media=$mediaError',
      );
    }
  }

  Future<void> _handleControl(AvaRealtimeServerEventV4 event) async {
    if (event.sessionId != _sessionId) {
      throw const AvaRealtimeRuntimeException(
        'Server event sessionId does not match session bootstrap.',
      );
    }
    if (event.type == AvaRealtimeServerEventTypeV4.turnCreated) {
      if (_turnId != null && !_terminal) {
        throw const AvaRealtimeRuntimeException(
          'A new turn cannot replace an active realtime turn.',
        );
      }
      _resetForNextTurn(event);
    } else {
      _acceptTurnEvent(event);
    }

    switch (event.type) {
      case AvaRealtimeServerEventTypeV4.turnCreated:
        _turnId = event.turnId;
        _trace?.mark('turn.created', fields: {'turnId': event.turnId});
        _presentation.beginTurn(
          sessionId: event.sessionId,
          turnId: event.turnId!,
        );
        canvasController.beginPlaybackClock(event.turnId!);
        break;
      case AvaRealtimeServerEventTypeV4.turnPhaseChanged:
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
      case AvaRealtimeServerEventTypeV4.segmentReady:
        _speechSegmentCount += 1;
        _speechCharacterCount += event.segment!.speech.runes.length;
        if (!_firstSegmentTraced) {
          _firstSegmentTraced = true;
          _trace?.mark(
            'response.first_segment',
            fields: {
              'turnId': _requireTurnId(),
              'segmentIndex': event.segmentIndex,
              'speechCharacterCount': event.segment!.speech.runes.length,
            },
          );
        }
        _presentation.addSegment(event.segmentIndex!, event.segment!);
        break;
      case AvaRealtimeServerEventTypeV4.audioMetadata:
        await _startPlayback(event);
        break;
      case AvaRealtimeServerEventTypeV4.alignmentBatch:
        _presentation.addAlignmentSpans(event.alignmentSpans);
        break;
      case AvaRealtimeServerEventTypeV4.interruptRequested:
        if (event.streamId != _streamId) {
          throw const AvaRealtimeRuntimeException(
            'Provider interruption streamId does not match active playback.',
          );
        }
        final trace = _trace;
        if (trace != null) {
          final reasonCode = event.interruptReason == 'providerSpeechStarted'
              ? 'providerSpeechStarted'
              : 'serverInterruption';
          trace.mark(
            'microphone.interruption_requested',
            fields: {
              'turnId': _requireTurnId(),
              'streamId': event.streamId,
              'reasonCode': reasonCode,
            },
          );
          // Diagnostics must never delay the click-free fade or cancellation.
          unawaited(
            recordAvaRealtimeAudioDiagnostics(
              trace: trace,
              trigger: 'interruptionRequested',
              turnId: _requireTurnId(),
              streamId: event.streamId,
              interruptionReasonCode: reasonCode,
            ),
          );
        }
        await interrupt(reason: event.interruptReason!);
        break;
      case AvaRealtimeServerEventTypeV4.audioChunk:
        if (event.byteLength! > _limits.maxAudioChunkBytes) {
          throw AvaRealtimeBufferOverflow(
            'PCM descriptor exceeds the negotiated '
            '${_limits.maxAudioChunkBytes}-byte limit.',
          );
        }
        _pendingAudioDescriptor = event;
        break;
      case AvaRealtimeServerEventTypeV4.memoryPersistenceFailed:
        _presentation.recordMemoryPersistenceFailure(event.failure!);
        break;
      case AvaRealtimeServerEventTypeV4.turnCompleted:
      case AvaRealtimeServerEventTypeV4.turnCancelled:
      case AvaRealtimeServerEventTypeV4.turnFailed:
        await _handleTerminal(event);
        break;
      case AvaRealtimeServerEventTypeV4.canvasGenerationStarted:
      case AvaRealtimeServerEventTypeV4.canvasDocumentAvailable:
      case AvaRealtimeServerEventTypeV4.canvasPresentationCue:
      case AvaRealtimeServerEventTypeV4.canvasActivated:
      case AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded:
      case AvaRealtimeServerEventTypeV4.canvasRejected:
      case AvaRealtimeServerEventTypeV4.interactionRejected:
        throw StateError('Canvas event reached the speech runtime.');
    }
  }

  Future<void> _startPlayback(AvaRealtimeServerEventV4 event) async {
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
    _trace?.mark(
      'speech.playback_started',
      fields: {
        'turnId': _requireTurnId(),
        'streamId': _streamId,
        'sampleRateHz': 24000,
      },
    );
    _updateCanvasPlaybackClock(
      turnId: _requireTurnId(),
      renderedSampleCount: 0,
      event: 'playbackStarted',
    );
    await _sendClient(
      (sequence) => AvaRealtimeClientEventV4.playbackStarted(
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
      _trace?.mark(
        'audio.first_pcm',
        fields: {
          'turnId': _requireTurnId(),
          'streamId': _requireStreamId(),
          'pcmByteLength': bytes.length,
          'sampleCount': bytes.length ~/ 2,
        },
      );
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
    _traceFirstRenderedSample(result.renderedSampleCount);
    _trace?.mark(
      'speech.playback_completed',
      fields: _speechPlaybackFields(
        turnId: _requireTurnId(),
        renderedSampleCount: result.renderedSampleCount,
        termination: 'completed',
      ),
    );
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
    _traceFirstRenderedSample(progress.renderedSampleCount);
    final previous = _lastProgress;
    _lastProgress = progress;
    if (previous?.renderedSampleCount == progress.renderedSampleCount &&
        previous?.bufferedSampleCount == progress.bufferedSampleCount) {
      return;
    }
    _updateCanvasPlaybackClock(
      turnId: _requireTurnId(),
      renderedSampleCount: progress.renderedSampleCount,
      event: 'playbackProgress',
    );
    await _sendClient(
      (sequence) => AvaRealtimeClientEventV4.playbackProgress(
        sessionId: _sessionId,
        sequence: sequence,
        turnId: _requireTurnId(),
        streamId: _requireStreamId(),
        renderedSampleCount: progress.renderedSampleCount,
        bufferedSampleCount: progress.bufferedSampleCount,
      ),
    );
  }

  Future<void> _handleTerminal(AvaRealtimeServerEventV4 event) async {
    if (_pendingAudioDescriptor != null) {
      throw const AvaRealtimeRuntimeException(
        'Terminal event arrived before pending binary PCM.',
      );
    }
    final delivered = event.deliveredSampleCount!;
    final localRendered =
        _finalRenderedSampleCount ?? _lastProgress?.renderedSampleCount ?? 0;
    switch (event.type) {
      case AvaRealtimeServerEventTypeV4.turnCompleted:
        if (delivered != localRendered ||
            delivered != snapshot.receivedSampleCount) {
          throw AvaRealtimeRuntimeException(
            'Completed delivered samples $delivered do not match local '
            'rendered samples $localRendered.',
          );
        }
        break;
      case AvaRealtimeServerEventTypeV4.turnCancelled:
        if (delivered != localRendered) {
          throw AvaRealtimeRuntimeException(
            'Cancelled delivered samples $delivered do not match the local '
            'stop position $localRendered.',
          );
        }
        if (_playbackStarted &&
            _finalRenderedSampleCount == null &&
            !_interrupting) {
          await _speechOutput.stop();
        }
        break;
      case AvaRealtimeServerEventTypeV4.turnFailed:
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
    _trace?.mark(
      'speech.turn_terminal',
      fields: _speechPlaybackFields(
        turnId: _requireTurnId(),
        renderedSampleCount: delivered,
        termination: event.type.name,
      ),
    );
    _presentation.complete(event);
    if (!_turnTerminal.isCompleted) _turnTerminal.complete();
  }

  void _resetForNextTurn(AvaRealtimeServerEventV4 event) {
    if (event.revision != 0 || event.turnId == null) {
      throw const AvaRealtimeStateException(
        'turnCreated must establish a turn at revision zero.',
      );
    }
    _progressTimer?.cancel();
    _progressTimer = null;
    _pendingAudioDescriptor = null;
    _turnId = event.turnId;
    _streamId = null;
    _phase = AvaRealtimeTurnPhase.created;
    _revision = 0;
    _nextSegmentIndex = 0;
    _nextAudioChunkIndex = 0;
    _receivedSampleCount = 0;
    _validatedAudioStreamId = null;
    _cancelRequestId = null;
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
    _firstRenderedSampleTraced = false;
    _speechSegmentCount = 0;
    _speechCharacterCount = 0;
  }

  Map<String, Object?> _speechPlaybackFields({
    required String turnId,
    required int renderedSampleCount,
    required String termination,
    Map<String, Object?> extra = const {},
  }) =>
      {
        'turnId': turnId,
        if (_streamId case final value?) 'streamId': value,
        'renderedSampleCount': renderedSampleCount,
        'sampleRateHz': 24000,
        'renderedDurationMs': renderedSampleCount * 1000 / 24000,
        'segmentCount': _speechSegmentCount,
        'speechCharacterCount': _speechCharacterCount,
        'termination': termination,
        ...extra,
      };

  void _traceFirstRenderedSample(int renderedSampleCount) {
    if (_firstRenderedSampleTraced || renderedSampleCount <= 0) return;
    _firstRenderedSampleTraced = true;
    _trace?.mark(
      'speech.first_rendered_sample',
      fields: {
        'turnId': _requireTurnId(),
        'streamId': _requireStreamId(),
        'renderedSampleCount': renderedSampleCount,
        'sampleRateHz': 24000,
      },
    );
  }

  void _acceptServerSequence(AvaRealtimeServerEventV4 event) {
    if (event.sequence != _nextServerSequence) {
      throw AvaRealtimeStateException(
        'Expected protocol-v4 server sequence $_nextServerSequence, '
        'received ${event.sequence}.',
      );
    }
    _nextServerSequence += 1;
  }

  void _acceptTurnEvent(AvaRealtimeServerEventV4 event) {
    if (_terminal) {
      throw const AvaRealtimeStateException(
        'Event received after terminal turn state.',
      );
    }
    if (_turnId == null || _phase == null) {
      throw const AvaRealtimeStateException(
        'turnCreated must be the first speech event.',
      );
    }
    if (event.turnId != _turnId) {
      throw const AvaRealtimeStateException(
        'turnId changed during an active turn.',
      );
    }
    switch (event.type) {
      case AvaRealtimeServerEventTypeV4.turnPhaseChanged:
        _acceptPhaseChange(event);
        break;
      case AvaRealtimeServerEventTypeV4.turnCompleted:
        _acceptCompleted(event);
        break;
      case AvaRealtimeServerEventTypeV4.turnCancelled:
        _acceptCancelled(event);
        break;
      case AvaRealtimeServerEventTypeV4.turnFailed:
        _acceptFailed(event);
        break;
      case AvaRealtimeServerEventTypeV4.segmentReady:
      case AvaRealtimeServerEventTypeV4.audioMetadata:
      case AvaRealtimeServerEventTypeV4.audioChunk:
      case AvaRealtimeServerEventTypeV4.alignmentBatch:
      case AvaRealtimeServerEventTypeV4.interruptRequested:
      case AvaRealtimeServerEventTypeV4.memoryPersistenceFailed:
        if (event.revision != _revision) {
          throw AvaRealtimeStateException(
            'Event revision ${event.revision} does not match active revision '
            '$_revision.',
          );
        }
        _acceptNonPhaseEvent(event);
        break;
      case AvaRealtimeServerEventTypeV4.turnCreated:
        throw const AvaRealtimeStateException('turnCreated cannot repeat.');
      case AvaRealtimeServerEventTypeV4.canvasGenerationStarted:
      case AvaRealtimeServerEventTypeV4.canvasDocumentAvailable:
      case AvaRealtimeServerEventTypeV4.canvasPresentationCue:
      case AvaRealtimeServerEventTypeV4.canvasActivated:
      case AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded:
      case AvaRealtimeServerEventTypeV4.canvasRejected:
      case AvaRealtimeServerEventTypeV4.interactionRejected:
        throw StateError('Canvas event reached turn validation.');
    }
  }

  void _acceptPhaseChange(AvaRealtimeServerEventV4 event) {
    final current = _requirePhase();
    final next = event.phase;
    if (next == null || !_allowedNextPhases(current).contains(next)) {
      throw AvaRealtimeStateException(
        'Invalid turn phase transition $current -> $next.',
      );
    }
    if (event.revision != _revision + 1) {
      throw const AvaRealtimeStateException(
        'Phase changes must increment revision by one.',
      );
    }
    if (next == AvaRealtimeTurnPhase.speaking &&
        _validatedAudioStreamId == null) {
      throw const AvaRealtimeStateException(
        'Speaking requires prior audioMetadata.',
      );
    }
    _phase = next;
    _revision = event.revision;
  }

  void _acceptNonPhaseEvent(AvaRealtimeServerEventV4 event) {
    final phase = _requirePhase();
    switch (event.type) {
      case AvaRealtimeServerEventTypeV4.segmentReady:
        if (phase != AvaRealtimeTurnPhase.planning &&
            phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'segmentReady requires planning or synthesizing.',
          );
        }
        if (event.segmentIndex != _nextSegmentIndex) {
          throw AvaRealtimeStateException(
            'Expected segmentIndex $_nextSegmentIndex, '
            'received ${event.segmentIndex}.',
          );
        }
        _nextSegmentIndex += 1;
        break;
      case AvaRealtimeServerEventTypeV4.audioMetadata:
        if (phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'audioMetadata requires synthesizing.',
          );
        }
        if (_validatedAudioStreamId != null) {
          throw const AvaRealtimeStateException(
            'audioMetadata may occur only once.',
          );
        }
        _validatedAudioStreamId = event.streamId;
        break;
      case AvaRealtimeServerEventTypeV4.audioChunk:
        if (phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'audioChunk requires synthesizing.',
          );
        }
        _requireAudioStream(event.streamId);
        if (event.chunkIndex != _nextAudioChunkIndex) {
          throw AvaRealtimeStateException(
            'Expected audio chunk $_nextAudioChunkIndex, '
            'received ${event.chunkIndex}.',
          );
        }
        if (event.sampleOffset != _receivedSampleCount) {
          throw AvaRealtimeStateException(
            'Expected sampleOffset $_receivedSampleCount, '
            'received ${event.sampleOffset}.',
          );
        }
        _nextAudioChunkIndex += 1;
        _receivedSampleCount += event.sampleCount!;
        break;
      case AvaRealtimeServerEventTypeV4.alignmentBatch:
        if (phase != AvaRealtimeTurnPhase.synthesizing) {
          throw const AvaRealtimeStateException(
            'alignmentBatch requires synthesizing.',
          );
        }
        _requireAudioStream(event.streamId);
        break;
      case AvaRealtimeServerEventTypeV4.interruptRequested:
        if (phase != AvaRealtimeTurnPhase.synthesizing &&
            phase != AvaRealtimeTurnPhase.speaking) {
          throw const AvaRealtimeStateException(
            'interruptRequested requires synthesizing or speaking.',
          );
        }
        _requireAudioStream(event.streamId);
        break;
      case AvaRealtimeServerEventTypeV4.memoryPersistenceFailed:
        if (phase != AvaRealtimeTurnPhase.listening &&
            phase != AvaRealtimeTurnPhase.speaking) {
          throw const AvaRealtimeStateException(
            'memoryPersistenceFailed requires listening or speaking.',
          );
        }
        break;
      case AvaRealtimeServerEventTypeV4.turnCreated:
      case AvaRealtimeServerEventTypeV4.turnPhaseChanged:
      case AvaRealtimeServerEventTypeV4.turnCompleted:
      case AvaRealtimeServerEventTypeV4.turnCancelled:
      case AvaRealtimeServerEventTypeV4.turnFailed:
      case AvaRealtimeServerEventTypeV4.canvasGenerationStarted:
      case AvaRealtimeServerEventTypeV4.canvasDocumentAvailable:
      case AvaRealtimeServerEventTypeV4.canvasPresentationCue:
      case AvaRealtimeServerEventTypeV4.canvasActivated:
      case AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded:
      case AvaRealtimeServerEventTypeV4.canvasRejected:
      case AvaRealtimeServerEventTypeV4.interactionRejected:
        throw AvaRealtimeStateException(
          'Unsupported non-phase event ${event.type.name}.',
        );
    }
  }

  void _acceptCompleted(AvaRealtimeServerEventV4 event) {
    if (_requirePhase() != AvaRealtimeTurnPhase.speaking) {
      throw const AvaRealtimeStateException(
        'turnCompleted requires speaking.',
      );
    }
    _acceptTerminalRevision(event.revision);
    _validateDeliveredSamples(event.deliveredSampleCount!);
    if (_nextAudioChunkIndex == 0) {
      throw const AvaRealtimeStateException(
        'turnCompleted requires received audio.',
      );
    }
    _phase = AvaRealtimeTurnPhase.completed;
    _terminal = true;
  }

  void _acceptCancelled(AvaRealtimeServerEventV4 event) {
    if (_requirePhase() != AvaRealtimeTurnPhase.cancelling) {
      throw const AvaRealtimeStateException(
        'turnCancelled requires cancelling.',
      );
    }
    _acceptTerminalRevision(event.revision);
    _validateDeliveredSamples(event.deliveredSampleCount!);
    _phase = AvaRealtimeTurnPhase.cancelled;
    _terminal = true;
  }

  void _acceptFailed(AvaRealtimeServerEventV4 event) {
    _acceptTerminalRevision(event.revision);
    _validateDeliveredSamples(event.deliveredSampleCount!);
    _phase = AvaRealtimeTurnPhase.failed;
    _terminal = true;
  }

  void _acceptTerminalRevision(int revision) {
    if (revision != _revision + 1) {
      throw const AvaRealtimeStateException(
        'Terminal events must increment revision by one.',
      );
    }
    _revision = revision;
  }

  void _validateDeliveredSamples(int samples) {
    if (samples > _receivedSampleCount) {
      throw const AvaRealtimeStateException(
        'Delivered samples cannot exceed received samples.',
      );
    }
  }

  bool _registerCancellation(String requestId) {
    if (requestId.isEmpty) {
      throw const AvaRealtimeStateException(
        'Cancel requestId cannot be empty.',
      );
    }
    final registered = _cancelRequestId;
    if (registered != null) {
      if (registered == requestId) return false;
      throw const AvaRealtimeStateException(
        'A different cancellation request is already registered.',
      );
    }
    if (_terminal) {
      throw const AvaRealtimeStateException(
        'Cannot cancel a terminal turn.',
      );
    }
    _requirePhase();
    _cancelRequestId = requestId;
    return true;
  }

  AvaRealtimeTurnPhase _requirePhase() {
    final phase = _phase;
    if (phase == null) {
      throw const AvaRealtimeStateException(
        'Turn state has not been created.',
      );
    }
    return phase;
  }

  void _requireAudioStream(String? streamId) {
    if (_validatedAudioStreamId == null) {
      throw const AvaRealtimeStateException(
        'Audio event requires audioMetadata.',
      );
    }
    if (streamId != _validatedAudioStreamId) {
      throw const AvaRealtimeStateException(
        'Audio streamId changed mid-turn.',
      );
    }
  }

  void _dispatchCanvasEvent(AvaRealtimeServerEventV4 event) {
    final json = event.toJson();
    switch (event.type) {
      case AvaRealtimeServerEventTypeV4.canvasGenerationStarted:
        canvasController.receiveGenerationStarted(json);
        break;
      case AvaRealtimeServerEventTypeV4.canvasDocumentAvailable:
        AvaSvgCanvasDocumentDescriptorV4.fromJson(json);
        unawaited(
          canvasController
              .receiveDocumentAvailable(json)
              .catchError(_recordAsynchronousFailure),
        );
        break;
      case AvaRealtimeServerEventTypeV4.canvasPresentationCue:
        canvasController.receivePresentationCue(json);
        break;
      case AvaRealtimeServerEventTypeV4.canvasActivated:
        canvasController.receiveActivated(json);
        break;
      case AvaRealtimeServerEventTypeV4.canvasDocumentDiscarded:
        canvasController.receiveDocumentDiscarded(json);
        break;
      case AvaRealtimeServerEventTypeV4.canvasRejected:
        canvasController.receiveRejected(json);
        break;
      case AvaRealtimeServerEventTypeV4.interactionRejected:
        canvasController.receiveInteractionRejected(json);
        break;
      default:
        throw StateError('Non-canvas event reached the SVG dispatcher.');
    }
  }

  void _enqueueCanvasCommand(AvaSvgCanvasClientCommand command) {
    if (_closed) return;
    final identity = _canvasIdentity(command);
    final event = AvaRealtimeClientEventV4.canvasCommand(
      sessionId: _sessionId,
      sequence: _takeClientSequence(),
      body: command.toEventBody(),
    );
    if (identity != null) {
      _recordCanvasCommand('canvas.client.command_enqueued', command, identity);
    }
    final operation = _enqueue(event);
    unawaited(
      operation.then<void>((_) {
        if (identity != null) {
          _recordCanvasCommand('canvas.client.command_sent', command, identity);
        }
      }).catchError(_recordAsynchronousFailure),
    );
  }

  ({String generationId, String sourceTurnId})? _canvasIdentity(
    AvaSvgCanvasClientCommand command,
  ) {
    final telemetryGenerationId = command.telemetryGenerationId;
    final telemetrySourceTurnId = command.telemetrySourceTurnId;
    if (telemetryGenerationId != null && telemetrySourceTurnId != null) {
      return (
        generationId: telemetryGenerationId,
        sourceTurnId: telemetrySourceTurnId,
      );
    }
    final generationId = command.fields['generationId'];
    if (generationId is! String) return null;
    final candidate = canvasController.candidate?.descriptor;
    if (candidate?.generationId == generationId) {
      return (
        generationId: generationId,
        sourceTurnId: candidate!.sourceTurnId,
      );
    }
    final active = canvasController.active?.document.descriptor;
    if (active?.generationId == generationId) {
      return (
        generationId: generationId,
        sourceTurnId: active!.sourceTurnId,
      );
    }
    final generation = canvasController.generation;
    if (generation?.generationId == generationId) {
      return (
        generationId: generationId,
        sourceTurnId: generation!.sourceTurnId,
      );
    }
    return null;
  }

  void _recordCanvasCommand(
    String stage,
    AvaSvgCanvasClientCommand command,
    ({String generationId, String sourceTurnId}) identity,
  ) {
    _canvasTelemetry?.call(
      AvaSvgCanvasTelemetryEvent(
        stage: stage,
        fields: {
          'generationId': identity.generationId,
          'sourceTurnId': identity.sourceTurnId,
          'command': command.type.name,
        },
      ),
    );
  }

  void _updateCanvasPlaybackClock({
    required String turnId,
    required int renderedSampleCount,
    required String event,
  }) {
    if (turnId != canvasController.renderedSampleTurnId) {
      if (turnId == canvasController.previousRenderedSampleTurnId) {
        developer.log(
          'stale_playback_control_dropped session=$_sessionId '
          'turn=$turnId current_turn=${canvasController.renderedSampleTurnId} '
          'event=$event',
          name: 'ava.realtime.protocol_v4',
        );
        return;
      }
      throw AvaRealtimeStateException(
        'Playback control for $turnId cannot mutate canvas clock '
        '${canvasController.renderedSampleTurnId ?? 'without an owner'}.',
      );
    }
    canvasController.updateRenderedSample(
      turnId: turnId,
      renderedSample: renderedSampleCount,
    );
  }

  Future<void> _enqueue(AvaRealtimeClientEventV4 event) {
    final operation = _sendTail.then((_) => _transport.send(event));
    _sendTail = operation;
    return operation;
  }

  int _takeClientSequence() {
    if (_nextClientSequence > 1000000) {
      throw const AvaRealtimeStateException(
        'Protocol-v4 client sequence exhausted its bound.',
      );
    }
    final result = _nextClientSequence;
    _nextClientSequence += 1;
    return result;
  }

  Future<void> _setMicrophoneEnabled(bool enabled) {
    final operation = _microphoneTail.then((_) async {
      if (_closed) return;
      await _mediaSession.setMicrophoneEnabled(enabled);
      final actual = _mediaSession.microphoneEnabled;
      if (actual != enabled) {
        throw AvaRealtimeRuntimeException(
          'Microphone track state did not match the requested state.',
        );
      }
      _onMicrophoneTrackChanged?.call(actual);
    });
    _microphoneTail = operation;
    return operation;
  }

  Future<void> _sendClient(
    AvaRealtimeClientEventV4 Function(int sequence) build,
  ) {
    final event = build(_takeClientSequence());
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

Set<AvaRealtimeTurnPhase> _allowedNextPhases(
  AvaRealtimeTurnPhase phase,
) =>
    switch (phase) {
      AvaRealtimeTurnPhase.created => const {
          AvaRealtimeTurnPhase.connecting,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.connecting => const {
          AvaRealtimeTurnPhase.listening,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.listening => const {
          AvaRealtimeTurnPhase.planning,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.planning => const {
          AvaRealtimeTurnPhase.synthesizing,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.synthesizing => const {
          AvaRealtimeTurnPhase.speaking,
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.speaking => const {
          AvaRealtimeTurnPhase.cancelling,
        },
      AvaRealtimeTurnPhase.cancelling => const {
          AvaRealtimeTurnPhase.cancelled,
        },
      AvaRealtimeTurnPhase.completed ||
      AvaRealtimeTurnPhase.cancelled ||
      AvaRealtimeTurnPhase.failed =>
        const {},
    };
