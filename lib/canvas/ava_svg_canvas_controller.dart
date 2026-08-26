import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/ava_svg_canvas_protocol.dart';
import '../services/ava_svg_document_service.dart';
import 'ava_svg_canvas_telemetry.dart';

enum AvaSvgCanvasCandidatePhase {
  fetching,
  rendererPreparing,
  prepared,
  presentationScheduled,
  presentationRequested,
  awaitingActivation,
}

enum AvaSvgCanvasCommandType {
  canvasDocumentPrepared,
  canvasPreparationFailed,
  canvasPresented,
  canvasRuntimeFailed,
  canvasInteraction,
}

@immutable
final class AvaSvgCanvasClientCommand {
  const AvaSvgCanvasClientCommand(
    this.type,
    this.fields, {
    this.telemetryGenerationId,
    this.telemetrySourceTurnId,
  }) : assert(
          (telemetryGenerationId == null) == (telemetrySourceTurnId == null),
          'Canvas command telemetry identity must be complete.',
        );

  final AvaSvgCanvasCommandType type;
  final Map<String, Object?> fields;
  final String? telemetryGenerationId;
  final String? telemetrySourceTurnId;

  Map<String, Object?> toEventBody() => {
        'event': type.name,
        ...fields,
      };
}

@immutable
final class AvaSvgCanvasGenerationState {
  const AvaSvgCanvasGenerationState({
    required this.generationId,
    required this.sourceTurnId,
  });

  final String generationId;
  final String sourceTurnId;
}

@immutable
final class AvaSvgCanvasCandidateState {
  const AvaSvgCanvasCandidateState({
    required this.descriptor,
    required this.phase,
    this.document,
    this.presentationSampleOffset,
    this.firstRenderedSample,
  });

  final AvaSvgCanvasDocumentDescriptorV4 descriptor;
  final AvaSvgCanvasCandidatePhase phase;
  final AvaValidatedSvgCanvasDocument? document;
  final int? presentationSampleOffset;
  final int? firstRenderedSample;

  AvaSvgCanvasCandidateState copyWith({
    AvaSvgCanvasCandidatePhase? phase,
    AvaValidatedSvgCanvasDocument? document,
    int? presentationSampleOffset,
    int? firstRenderedSample,
  }) =>
      AvaSvgCanvasCandidateState(
        descriptor: descriptor,
        phase: phase ?? this.phase,
        document: document ?? this.document,
        presentationSampleOffset:
            presentationSampleOffset ?? this.presentationSampleOffset,
        firstRenderedSample: firstRenderedSample ?? this.firstRenderedSample,
      );
}

@immutable
final class AvaSvgCanvasActiveState {
  const AvaSvgCanvasActiveState({
    required this.document,
    required this.firstRenderedSample,
  });

  final AvaValidatedSvgCanvasDocument document;
  final int firstRenderedSample;
}

@immutable
final class AvaSvgCanvasIssue {
  const AvaSvgCanvasIssue({
    required this.code,
    required this.message,
    required this.generationId,
    this.bindingId,
    this.callId,
  });

  final String code;
  final String message;
  final String? generationId;
  final String? bindingId;
  final String? callId;
}

typedef AvaSvgCanvasCommandSink = FutureOr<void> Function(
  AvaSvgCanvasClientCommand command,
);

typedef AvaRetainedSceneRevisionLookup = int? Function(String sceneId);

final class AvaSvgCanvasController extends ChangeNotifier {
  AvaSvgCanvasController({
    required AvaSvgDocumentLoader documents,
    required AvaSvgCanvasCommandSink commandSink,
    AvaSvgCanvasTelemetrySink? telemetry,
    AvaRetainedSceneRevisionLookup? retainedSceneRevision,
  })  : _documents = documents,
        _commandSink = commandSink,
        _telemetry = telemetry,
        _retainedSceneRevision = retainedSceneRevision ?? _unretainedScene {
    _clock.start();
  }

  final AvaSvgDocumentLoader _documents;
  final AvaSvgCanvasCommandSink _commandSink;
  final AvaSvgCanvasTelemetrySink? _telemetry;
  final AvaRetainedSceneRevisionLookup _retainedSceneRevision;
  final Stopwatch _clock = Stopwatch();
  AvaSvgCanvasGenerationState? _generation;
  AvaSvgCanvasCandidateState? _candidate;
  AvaSvgCanvasActiveState? _active;
  AvaSvgCanvasIssue? _lastIssue;
  int _loadGeneration = 0;
  String? _previousRenderedSampleTurnId;
  String? _renderedSampleTurnId;
  int _lastRenderedSample = 0;
  final Map<String, int> _renderedSamplesByTurn = {};
  int _interactionRevision = 0;
  String? _failedActiveRendererGenerationId;
  bool _closed = false;
  ({String generationId, Duration startedAt})? _rendererPreparation;
  ({String generationId, Duration startedAt})? _presentationRequest;

  AvaSvgCanvasGenerationState? get generation => _generation;
  AvaSvgCanvasCandidateState? get candidate => _candidate;
  AvaSvgCanvasActiveState? get active => _active;
  AvaSvgCanvasIssue? get lastIssue => _lastIssue;
  bool get activeRendererHealthy {
    final activeGenerationId = _active?.document.descriptor.generationId;
    return activeGenerationId != null &&
        _failedActiveRendererGenerationId != activeGenerationId;
  }

  String? get previousRenderedSampleTurnId => _previousRenderedSampleTurnId;
  String? get renderedSampleTurnId => _renderedSampleTurnId;

  AvaValidatedSvgCanvasDocument? get documentForRenderer =>
      _candidate?.document ?? _active?.document;

  bool get shouldPresentCandidate =>
      _candidate?.phase == AvaSvgCanvasCandidatePhase.presentationRequested ||
      _candidate?.phase == AvaSvgCanvasCandidatePhase.awaitingActivation;

  void receiveGenerationStarted(Map<String, Object?> event) {
    _ensureOpen();
    _requireExactKeys(
      event,
      const {
        'protocolVersion',
        'event',
        'sessionId',
        'sequence',
        'revision',
        'generationId',
        'sourceTurnId',
      },
      'canvasGenerationStarted',
    );
    if (event['protocolVersion'] != 4 ||
        event['event'] != 'canvasGenerationStarted') {
      throw const FormatException('Canvas generation start requires v4.');
    }
    final generationId = event['generationId'];
    final sourceTurnId = event['sourceTurnId'];
    if (generationId is! String ||
        generationId.isEmpty ||
        sourceTurnId is! String ||
        sourceTurnId.isEmpty) {
      throw const FormatException(
        'Canvas generation start identity is invalid.',
      );
    }
    if (_generation != null || _candidate != null) {
      throw StateError(
        'canvasGenerationStarted requires no pending canvas work.',
      );
    }
    _generation = AvaSvgCanvasGenerationState(
      generationId: generationId,
      sourceTurnId: sourceTurnId,
    );
    _recordGeneration(
      'canvas.generation.started_received',
      _generation!,
    );
    _lastIssue = null;
    _pruneRenderedSamples();
    notifyListeners();
  }

  Future<void> receiveDocumentAvailable(
    Map<String, Object?> event,
  ) async {
    _ensureOpen();
    if (_candidate != null) {
      throw StateError(
        'canvasDocumentAvailable requires the previous candidate to terminate.',
      );
    }
    final descriptor = AvaSvgCanvasDocumentDescriptorV4.fromJson(event);
    final generation = _generation;
    if (generation == null) {
      throw StateError(
        'canvasDocumentAvailable requires an accepted generation.',
      );
    }
    if (generation.generationId != descriptor.generationId ||
        generation.sourceTurnId != descriptor.sourceTurnId) {
      throw const FormatException(
        'Canvas document does not match the accepted generation.',
      );
    }
    _recordDescriptor('canvas.document.available_received', descriptor);
    final activeDescriptor = _active?.document.descriptor;
    final retainedRevision = _retainedSceneRevision(descriptor.sceneId);
    if (activeDescriptor?.sceneId == descriptor.sceneId &&
        retainedRevision != null &&
        activeDescriptor!.sceneRevision != retainedRevision) {
      throw StateError(
        'The live canvas and retained workspace disagree on scene revision.',
      );
    }
    final knownRevision = retainedRevision ??
        (activeDescriptor?.sceneId == descriptor.sceneId
            ? activeDescriptor?.sceneRevision
            : null);
    if (knownRevision != null &&
        descriptor.sceneRevision != knownRevision + 1) {
      throw const FormatException(
        'Canvas candidate must monotonically revise its retained scene.',
      );
    }
    if (knownRevision == null && descriptor.sceneRevision != 1) {
      throw const FormatException(
        'First canvas candidate must have sceneRevision 1.',
      );
    }
    final loadGeneration = ++_loadGeneration;
    _generation = null;
    _candidate = AvaSvgCanvasCandidateState(
      descriptor: descriptor,
      phase: AvaSvgCanvasCandidatePhase.fetching,
    );
    _lastIssue = null;
    notifyListeners();
    try {
      final document = await _documents.fetch(descriptor);
      if (_closed ||
          loadGeneration != _loadGeneration ||
          _candidate?.descriptor.generationId != descriptor.generationId) {
        return;
      }
      _candidate = _candidate!.copyWith(
        phase: AvaSvgCanvasCandidatePhase.rendererPreparing,
        document: document,
      );
      _rendererPreparation = (
        generationId: descriptor.generationId,
        startedAt: _clock.elapsed,
      );
      _recordDescriptor(
        'canvas.renderer.prepare_started',
        descriptor,
        fields: {
          'svgByteLength': document.svgBytes.length,
          'manifestByteLength': document.manifestBytes.length,
        },
      );
      notifyListeners();
    } on Object catch (error) {
      if (_closed ||
          loadGeneration != _loadGeneration ||
          _candidate?.descriptor.generationId != descriptor.generationId) {
        return;
      }
      final failureCode =
          error is FormatException ? 'contentIdentityMismatch' : 'fetchFailed';
      await _failCandidate(
        failureCode: failureCode,
        message: 'Ava could not prepare this drawing (${error.runtimeType}).',
      );
    }
  }

  void rendererPrepared() {
    _ensureOpen();
    final candidate = _requireCandidate(
      AvaSvgCanvasCandidatePhase.rendererPreparing,
    );
    _candidate = candidate.copyWith(
      phase: AvaSvgCanvasCandidatePhase.prepared,
    );
    _recordDescriptor(
      'canvas.renderer.prepared',
      candidate.descriptor,
      stageDuration: _elapsedFor(
        candidate.descriptor,
        _rendererPreparation,
      ),
    );
    _rendererPreparation = null;
    _recordDescriptor(
      'canvas.client.document_prepared_enqueued',
      candidate.descriptor,
    );
    _emit(
      AvaSvgCanvasClientCommand(
        AvaSvgCanvasCommandType.canvasDocumentPrepared,
        {
          ..._identityFields(candidate.descriptor),
          'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
          'validatorVersion': avaSvgValidatorVersion,
        },
        telemetryGenerationId: candidate.descriptor.generationId,
        telemetrySourceTurnId: candidate.descriptor.sourceTurnId,
      ),
    );
    notifyListeners();
  }

  void receivePresentationCue(Map<String, Object?> event) {
    _ensureOpen();
    _requireExactKeys(
      event,
      const {
        'protocolVersion',
        'event',
        'sessionId',
        'sequence',
        'revision',
        'generationId',
        'sourceTurnId',
        'sceneId',
        'sceneRevision',
        'presentationSampleOffset',
        'svgSha256',
        'manifestSha256',
      },
      'canvasPresentationCue',
    );
    if (event['protocolVersion'] != 4 ||
        event['event'] != 'canvasPresentationCue') {
      throw const FormatException('Canvas presentation cue requires v4.');
    }
    final candidate = _requireCandidate(AvaSvgCanvasCandidatePhase.prepared);
    _requireDescriptorIdentity(event, candidate.descriptor);
    final sample = event['presentationSampleOffset'];
    if (sample is! int || sample < 0 || sample > 2147483647) {
      throw const FormatException(
        'canvasPresentationCue.presentationSampleOffset is invalid.',
      );
    }
    _recordDescriptor(
      'canvas.presentation.cue_received',
      candidate.descriptor,
      fields: {'presentationSampleOffset': sample},
    );
    final sourceRenderedSample =
        _renderedSamplesByTurn[candidate.descriptor.sourceTurnId];
    if (sourceRenderedSample == null) {
      _lastIssue = AvaSvgCanvasIssue(
        code: 'stalePresentationCue',
        message: 'The drawing source playback clock is no longer available.',
        generationId: candidate.descriptor.generationId,
      );
      notifyListeners();
      return;
    }
    _candidate = candidate.copyWith(
      phase: AvaSvgCanvasCandidatePhase.presentationScheduled,
      presentationSampleOffset: sample,
    );
    _advancePresentationIfDue();
    notifyListeners();
  }

  void beginPlaybackClock(String turnId) {
    _ensureOpen();
    if (turnId.isEmpty) {
      throw const FormatException('Playback clock turnId cannot be empty.');
    }
    if (_renderedSampleTurnId == turnId) {
      throw StateError('Playback clock is already bound to turn $turnId.');
    }
    _previousRenderedSampleTurnId = _renderedSampleTurnId;
    _renderedSampleTurnId = turnId;
    _lastRenderedSample = 0;
    _renderedSamplesByTurn[turnId] = 0;
    _pruneRenderedSamples();
  }

  void updateRenderedSample({
    required String turnId,
    required int renderedSample,
  }) {
    _ensureOpen();
    if (_renderedSampleTurnId != turnId) {
      throw StateError(
        'Rendered sample belongs to $turnId, but the canvas clock belongs to '
        '${_renderedSampleTurnId ?? 'no turn'}.',
      );
    }
    if (renderedSample < _lastRenderedSample) {
      throw StateError('Rendered sample clock cannot move backwards.');
    }
    _lastRenderedSample = renderedSample;
    _renderedSamplesByTurn[turnId] = renderedSample;
    final advanced = _advancePresentationIfDue();
    if (advanced) notifyListeners();
  }

  void firstFramePresented() {
    _ensureOpen();
    final candidate = _requireCandidate(
      AvaSvgCanvasCandidatePhase.presentationRequested,
    );
    final sourceRenderedSample =
        _renderedSamplesByTurn[candidate.descriptor.sourceTurnId];
    final cue = candidate.presentationSampleOffset;
    if (sourceRenderedSample == null ||
        cue == null ||
        sourceRenderedSample < cue) {
      throw StateError('SVG first frame was reported before its sample cue.');
    }
    _candidate = candidate.copyWith(
      phase: AvaSvgCanvasCandidatePhase.awaitingActivation,
      firstRenderedSample: sourceRenderedSample,
    );
    _recordDescriptor(
      'canvas.presentation.first_frame',
      candidate.descriptor,
      stageDuration: _elapsedFor(
        candidate.descriptor,
        _presentationRequest,
      ),
      fields: {
        'presentationSampleOffset': cue,
        'firstRenderedSample': sourceRenderedSample,
      },
    );
    _presentationRequest = null;
    _recordDescriptor(
      'canvas.client.presented_enqueued',
      candidate.descriptor,
      fields: {'firstRenderedSample': sourceRenderedSample},
    );
    _emit(
      AvaSvgCanvasClientCommand(
        AvaSvgCanvasCommandType.canvasPresented,
        {
          ..._identityFields(candidate.descriptor),
          'firstRenderedSample': sourceRenderedSample,
        },
        telemetryGenerationId: candidate.descriptor.generationId,
        telemetrySourceTurnId: candidate.descriptor.sourceTurnId,
      ),
    );
    notifyListeners();
  }

  void receiveActivated(Map<String, Object?> event) {
    _ensureOpen();
    _requireExactKeys(
      event,
      const {
        'protocolVersion',
        'event',
        'sessionId',
        'sequence',
        'revision',
        'generationId',
        'sourceTurnId',
        'sceneId',
        'sceneRevision',
        'firstRenderedSample',
        'svgSha256',
        'manifestSha256',
      },
      'canvasActivated',
    );
    if (event['protocolVersion'] != 4 || event['event'] != 'canvasActivated') {
      throw const FormatException('Canvas activation requires protocol v4.');
    }
    final candidate = _requireCandidate(
      AvaSvgCanvasCandidatePhase.awaitingActivation,
    );
    _requireDescriptorIdentity(event, candidate.descriptor);
    if (event['firstRenderedSample'] != candidate.firstRenderedSample) {
      throw const FormatException(
        'canvasActivated changed the first-rendered sample.',
      );
    }
    final document = candidate.document;
    if (document == null || candidate.firstRenderedSample == null) {
      throw StateError('Activated candidate lost its prepared document.');
    }
    _active = AvaSvgCanvasActiveState(
      document: document,
      firstRenderedSample: candidate.firstRenderedSample!,
    );
    _candidate = null;
    _generation = null;
    _interactionRevision = 0;
    _failedActiveRendererGenerationId = null;
    _recordDescriptor(
      'canvas.activation.completed',
      document.descriptor,
      fields: {'firstRenderedSample': candidate.firstRenderedSample},
    );
    _pruneRenderedSamples();
    notifyListeners();
  }

  void reportBinding(AvaSvgInteractionBinding binding) {
    _ensureOpen();
    final active = _active;
    if (active == null || !activeRendererHealthy || !binding.serverReportable) {
      return;
    }
    final canonical = active.document.manifest.bindings[binding.bindingId];
    if (!identical(canonical, binding)) {
      throw StateError('SVG binding does not belong to the active manifest.');
    }
    _interactionRevision += 1;
    final descriptor = active.document.descriptor;
    _emit(
      AvaSvgCanvasClientCommand(
        AvaSvgCanvasCommandType.canvasInteraction,
        {
          'sceneId': descriptor.sceneId,
          'sceneRevision': descriptor.sceneRevision,
          'interactionRevision': _interactionRevision,
          'svgSha256': descriptor.svgSha256,
          'manifestSha256': descriptor.manifestSha256,
          'bindingId': binding.bindingId,
        },
        telemetryGenerationId: descriptor.generationId,
        telemetrySourceTurnId: descriptor.sourceTurnId,
      ),
    );
  }

  Future<void> rendererFailed(
    String message, {
    String? generationId,
  }) async {
    _ensureOpen();
    final candidate = _candidate;
    if (candidate != null &&
        (generationId == null ||
            candidate.descriptor.generationId == generationId)) {
      await _failCandidate(
        failureCode: message.contains('terminated')
            ? 'rendererTerminated'
            : 'rendererRejected',
        message: _boundedMessage(message),
      );
      return;
    }
    final active = _active;
    if (active == null ||
        (generationId != null &&
            active.document.descriptor.generationId != generationId)) {
      return;
    }
    final descriptor = active.document.descriptor;
    if (_failedActiveRendererGenerationId == descriptor.generationId) return;
    final failureCode = message.contains('terminated')
        ? 'rendererTerminated'
        : 'rendererRejected';
    _failedActiveRendererGenerationId = descriptor.generationId;
    _lastIssue = AvaSvgCanvasIssue(
      code: failureCode,
      message: _boundedMessage(message),
      generationId: descriptor.generationId,
    );
    _emit(
      AvaSvgCanvasClientCommand(
        AvaSvgCanvasCommandType.canvasRuntimeFailed,
        {
          ..._identityFields(descriptor),
          'failureCode': failureCode,
          'message': _boundedMessage(message),
        },
        telemetryGenerationId: descriptor.generationId,
        telemetrySourceTurnId: descriptor.sourceTurnId,
      ),
    );
    notifyListeners();
  }

  void receiveDocumentDiscarded(Map<String, Object?> event) {
    _ensureOpen();
    final generationId = event['generationId'];
    if (generationId is! String) {
      throw const FormatException(
        'canvasDocumentDiscarded.generationId is required.',
      );
    }
    if (_candidate?.descriptor.generationId == generationId) {
      _candidate = null;
      _loadGeneration += 1;
      _rendererPreparation = null;
      _presentationRequest = null;
    }
    if (_generation?.generationId == generationId) {
      _generation = null;
    }
    if (_active?.document.descriptor.generationId == generationId) {
      _active = null;
      if (_failedActiveRendererGenerationId == generationId) {
        _failedActiveRendererGenerationId = null;
      }
    }
    final sourceTurnId = event['sourceTurnId'];
    if (sourceTurnId is String) {
      _recordIdentity(
        'canvas.document.discarded',
        generationId: generationId,
        sourceTurnId: sourceTurnId,
        fields: {'reason': event['reason']},
      );
    }
    _pruneRenderedSamples();
    notifyListeners();
  }

  void receiveRejected(Map<String, Object?> event) {
    _ensureOpen();
    final generationId = event['generationId'];
    final callId = event['callId'];
    final code = event['rejectionCode'];
    final message = event['message'];
    if ((generationId is String) == (callId is String) ||
        code is! String ||
        message is! String) {
      throw const FormatException('canvasRejected fields are invalid.');
    }
    if (_candidate?.descriptor.generationId == generationId) {
      _candidate = null;
      _loadGeneration += 1;
      _rendererPreparation = null;
      _presentationRequest = null;
    }
    if (_generation?.generationId == generationId) {
      _generation = null;
    }
    // `cancelled` is the structured V4 result for optional canvas work that
    // was retired by the owning turn's normal interruption path. It clears
    // pending work but is not a canvas failure and must not replace the
    // current active scene or create a user-visible error.
    _lastIssue = code == 'cancelled'
        ? null
        : AvaSvgCanvasIssue(
            code: code,
            message: _boundedMessage(message),
            generationId: generationId as String?,
            callId: callId as String?,
          );
    final sourceTurnId = event['sourceTurnId'];
    if (generationId is String && sourceTurnId is String) {
      _recordIdentity(
        'canvas.generation.rejected',
        generationId: generationId,
        sourceTurnId: sourceTurnId,
        fields: {'rejectionCode': code},
      );
    }
    _pruneRenderedSamples();
    notifyListeners();
  }

  void receiveInteractionRejected(Map<String, Object?> event) {
    _ensureOpen();
    final sceneId = event['sceneId'];
    final sceneRevision = event['sceneRevision'];
    final bindingId = event['bindingId'];
    final code = event['rejectionCode'];
    final message = event['message'];
    if (sceneId is! String ||
        sceneRevision is! int ||
        bindingId is! String ||
        code is! String ||
        message is! String) {
      throw const FormatException('interactionRejected fields are invalid.');
    }
    final activeDescriptor = _active?.document.descriptor;
    _lastIssue = AvaSvgCanvasIssue(
      code: code,
      message: _boundedMessage(message),
      generationId: activeDescriptor?.sceneId == sceneId &&
              activeDescriptor?.sceneRevision == sceneRevision
          ? activeDescriptor?.generationId
          : null,
      bindingId: bindingId,
    );
    notifyListeners();
  }

  @override
  void dispose() {
    if (!_closed) {
      _closed = true;
      _loadGeneration += 1;
      _rendererPreparation = null;
      _presentationRequest = null;
      _documents.close();
    }
    super.dispose();
  }

  bool _advancePresentationIfDue() {
    final candidate = _candidate;
    if (candidate?.phase != AvaSvgCanvasCandidatePhase.presentationScheduled) {
      return false;
    }
    final renderedSample =
        _renderedSamplesByTurn[candidate!.descriptor.sourceTurnId];
    final cue = candidate.presentationSampleOffset;
    if (renderedSample == null || cue == null || renderedSample < cue) {
      return false;
    }
    _candidate = candidate.copyWith(
      phase: AvaSvgCanvasCandidatePhase.presentationRequested,
    );
    _presentationRequest = (
      generationId: candidate.descriptor.generationId,
      startedAt: _clock.elapsed,
    );
    _recordDescriptor(
      'canvas.presentation.requested',
      candidate.descriptor,
      fields: {
        'presentationSampleOffset': cue,
        'renderedSample': renderedSample,
      },
    );
    return true;
  }

  void _pruneRenderedSamples() {
    final retained = <String>{
      if (_renderedSampleTurnId case final value?) value,
      if (_previousRenderedSampleTurnId case final value?) value,
      if (_generation?.sourceTurnId case final value?) value,
      if (_candidate?.descriptor.sourceTurnId case final value?) value,
    };
    _renderedSamplesByTurn.removeWhere(
      (turnId, _) => !retained.contains(turnId),
    );
  }

  AvaSvgCanvasCandidateState _requireCandidate(
    AvaSvgCanvasCandidatePhase phase,
  ) {
    final candidate = _candidate;
    if (candidate == null || candidate.phase != phase) {
      throw StateError('SVG candidate is not in ${phase.name}.');
    }
    return candidate;
  }

  Future<void> _failCandidate({
    required String failureCode,
    required String message,
  }) async {
    final candidate = _candidate;
    if (candidate == null) return;
    final descriptor = candidate.descriptor;
    _candidate = null;
    _loadGeneration += 1;
    _rendererPreparation = null;
    _presentationRequest = null;
    _lastIssue = AvaSvgCanvasIssue(
      code: failureCode,
      message: _boundedMessage(message),
      generationId: descriptor.generationId,
    );
    _recordDescriptor(
      'canvas.document.preparation_failed',
      descriptor,
      fields: {'failureCode': failureCode},
    );
    _pruneRenderedSamples();
    await Future.sync(
      () => _commandSink(
        AvaSvgCanvasClientCommand(
          AvaSvgCanvasCommandType.canvasPreparationFailed,
          {
            ..._identityFields(descriptor),
            'failureCode': failureCode,
            'message': _boundedMessage(message),
          },
          telemetryGenerationId: descriptor.generationId,
          telemetrySourceTurnId: descriptor.sourceTurnId,
        ),
      ),
    );
    notifyListeners();
  }

  void _emit(AvaSvgCanvasClientCommand command) {
    unawaited(Future.sync(() => _commandSink(command)));
  }

  void _ensureOpen() {
    if (_closed) throw StateError('SVG canvas controller is closed.');
  }

  Duration _elapsedFor(
    AvaSvgCanvasDocumentDescriptorV4 descriptor,
    ({String generationId, Duration startedAt})? timing,
  ) {
    if (timing == null) return Duration.zero;
    if (timing.generationId != descriptor.generationId) {
      throw StateError(
        'Canvas telemetry timing belongs to ${timing.generationId}, not '
        '${descriptor.generationId}.',
      );
    }
    final elapsed = _clock.elapsed - timing.startedAt;
    return elapsed.isNegative ? Duration.zero : elapsed;
  }

  void _recordGeneration(
    String stage,
    AvaSvgCanvasGenerationState generation, {
    Duration stageDuration = Duration.zero,
    Map<String, Object?> fields = const {},
  }) {
    _recordIdentity(
      stage,
      generationId: generation.generationId,
      sourceTurnId: generation.sourceTurnId,
      stageDuration: stageDuration,
      fields: fields,
    );
  }

  void _recordDescriptor(
    String stage,
    AvaSvgCanvasDocumentDescriptorV4 descriptor, {
    Duration stageDuration = Duration.zero,
    Map<String, Object?> fields = const {},
  }) {
    _recordIdentity(
      stage,
      generationId: descriptor.generationId,
      sourceTurnId: descriptor.sourceTurnId,
      stageDuration: stageDuration,
      fields: {
        'sceneId': descriptor.sceneId,
        'sceneRevision': descriptor.sceneRevision,
        'svgSha256': descriptor.svgSha256,
        'manifestSha256': descriptor.manifestSha256,
        ...fields,
      },
    );
  }

  void _recordIdentity(
    String stage, {
    required String generationId,
    required String sourceTurnId,
    Duration stageDuration = Duration.zero,
    Map<String, Object?> fields = const {},
  }) {
    _telemetry?.call(
      AvaSvgCanvasTelemetryEvent(
        stage: stage,
        stageDuration: stageDuration,
        fields: {
          'generationId': generationId,
          'sourceTurnId': sourceTurnId,
          ...fields,
        },
      ),
    );
  }
}

int? _unretainedScene(String sceneId) => null;

Map<String, Object?> _identityFields(
  AvaSvgCanvasDocumentDescriptorV4 descriptor,
) =>
    {
      'generationId': descriptor.generationId,
      'sceneId': descriptor.sceneId,
      'sceneRevision': descriptor.sceneRevision,
      'svgSha256': descriptor.svgSha256,
      'manifestSha256': descriptor.manifestSha256,
    };

void _requireDescriptorIdentity(
  Map<String, Object?> event,
  AvaSvgCanvasDocumentDescriptorV4 descriptor,
) {
  if (event['generationId'] != descriptor.generationId ||
      event['sourceTurnId'] != descriptor.sourceTurnId ||
      event['sceneId'] != descriptor.sceneId ||
      event['sceneRevision'] != descriptor.sceneRevision ||
      event['svgSha256'] != descriptor.svgSha256 ||
      event['manifestSha256'] != descriptor.manifestSha256) {
    throw const FormatException(
      'Canvas event content identity does not match the pending document.',
    );
  }
}

String _boundedMessage(String value) {
  if (value.isEmpty) return 'Canvas failed without an error message.';
  return value.length <= 240 ? value : value.substring(0, 240);
}

void _requireExactKeys(
  Map<String, Object?> json,
  Set<String> expected,
  String label,
) {
  final actual = json.keys.toSet();
  if (actual.length != expected.length || !actual.containsAll(expected)) {
    throw FormatException('$label contains missing or unknown fields.');
  }
}
