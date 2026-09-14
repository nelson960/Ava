import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/foundation.dart';

import '../models/ava_svg_canvas_protocol.dart';
import '../models/ava_realtime_protocol_v4.dart';
import 'ava_svg_canvas_controller.dart';

typedef AvaCanvasRenderProfileUpdateSink = void Function(
  int profileRevision,
  AvaCanvasRenderProfileV4 renderProfile,
);

final class AvaRestoredCanvasWorkspace {
  const AvaRestoredCanvasWorkspace({
    required this.documents,
    required this.focusedSceneId,
    this.workspaceEpoch = 0,
    this.workspaceRevision = 0,
    this.nextCreationSequence = 1,
    this.nextModificationSequence = 1,
    this.creationSequences = const {},
    this.modificationSequences = const {},
  });

  final List<AvaValidatedSvgCanvasDocument> documents;
  final String? focusedSceneId;
  final int workspaceEpoch;
  final int workspaceRevision;
  final int nextCreationSequence;
  final int nextModificationSequence;
  final Map<String, int> creationSequences;
  final Map<String, int?> modificationSequences;
}

typedef AvaCanvasRuntimeFocusSink = Future<void> Function(
  AvaValidatedSvgCanvasDocument document,
  int focusRevision,
);

abstract interface class AvaCanvasPersistence {
  Future<AvaRestoredCanvasWorkspace> loadWorkspace();

  Future<void> persistActive(AvaValidatedSvgCanvasDocument document,
      {bool focused = true});

  Future<void> persistFocus(String sceneId);

  Future<void> reset();
}

abstract interface class AvaStructuralCanvasPersistence
    implements AvaCanvasPersistence {
  Future<void> persistStructuralActive(
    AvaValidatedSvgCanvasDocument document, {
    required bool focused,
    required int workspaceEpoch,
    required int workspaceRevision,
    required int creationSequence,
    required int modificationSequence,
    String? evictedSceneId,
  });
}

/// Application-level owner of the presented SVG and the controller attached
/// to the current realtime session.
final class AvaSvgCanvasSessionHost extends ChangeNotifier {
  static const int maximumSceneCount = 10;

  AvaSvgCanvasSessionHost({
    Duration renderProfileDebounce = const Duration(milliseconds: 150),
    Duration persistenceRestoreTimeout = const Duration(seconds: 2),
    AvaCanvasPersistence? persistence,
  })  : _renderProfileDebounce = renderProfileDebounce,
        _persistenceRestoreTimeout = persistenceRestoreTimeout,
        _persistence = persistence {
    if (renderProfileDebounce.isNegative) {
      throw ArgumentError.value(
        renderProfileDebounce,
        'renderProfileDebounce',
        'cannot be negative',
      );
    }
    if (persistenceRestoreTimeout <= Duration.zero) {
      throw ArgumentError.value(
        persistenceRestoreTimeout,
        'persistenceRestoreTimeout',
        'must be positive',
      );
    }
    _restoration = _restorePersistedWorkspace();
  }

  final Duration _renderProfileDebounce;
  final Duration _persistenceRestoreTimeout;
  final AvaCanvasPersistence? _persistence;
  late final Future<void> _restoration;
  bool _restoreInvalidated = false;
  int _workspaceEpoch = 0;
  int _structuralWorkspaceEpoch = 0;
  int _structuralWorkspaceRevision = 0;
  int _nextCreationSequence = 1;
  int _nextModificationSequence = 1;
  final Map<String, int> _creationSequences = {};
  final Map<String, int?> _modificationSequences = {};
  AvaSvgCanvasController? _controller;
  AvaCanvasRuntimeFocusSink? _runtimeFocusSink;
  bool _focusTransitionPending = false;
  int _focusRevision = 0;
  int _lastAppliedCanonicalRevision = 0;
  bool _acceptedPresentationVisible = false;
  String? _acceptedPresentationSceneId;
  int? _acceptedPresentationFocusRevision;

  int get focusRevision => _focusRevision;
  int get lastAppliedCanonicalRevision => _lastAppliedCanonicalRevision;

  AvaCanvasPresentationSnapshot presentationSnapshot() {
    final visible = panelVisible;
    final descriptor = _presentedDocument?.descriptor;
    final rev = _focusRevision > 0 ? _focusRevision : 1;
    return AvaCanvasPresentationSnapshot(
      visible: visible,
      sceneId: visible ? descriptor?.sceneId : null,
      focusRevision: visible ? rev : null,
    );
  }

  Map<String, Object?> turnContextSnapshot() {
    final descriptor = _presentedDocument?.descriptor;
    return {
      'focusRevision': _focusRevision,
      'focusedCanvas': descriptor == null
          ? null
          : {
              'sceneId': descriptor.sceneId,
              'sceneRevision': descriptor.sceneRevision,
              'svgSha256': descriptor.svgSha256,
              'manifestSha256': descriptor.manifestSha256,
            },
    };
  }

  final List<AvaValidatedSvgCanvasDocument> _workspaceDocuments = [];
  AvaValidatedSvgCanvasDocument? _presentedDocument;
  bool _panelDismissed = false;
  String? _dismissedGenerationId;
  String? _observedPendingGenerationId;
  AvaCanvasRenderProfileV4? _renderProfile;
  AvaCanvasRenderProfileV4? _lastSentRenderProfile;
  AvaCanvasRenderProfileUpdateSink? _renderProfileUpdateSink;
  Timer? _renderProfileTimer;
  int _renderProfileRevision = 0;

  AvaSvgCanvasController? get controller => _controller;
  List<AvaValidatedSvgCanvasDocument> get workspaceDocuments =>
      List.unmodifiable(_workspaceDocuments);
  int get sceneCount => _workspaceDocuments.length;
  AvaValidatedSvgCanvasDocument? get presentedDocument => _presentedDocument;
  bool get panelDismissed => _panelDismissed;
  AvaCanvasRenderProfileV4? get renderProfile => _renderProfile;
  Future<void> get restoration => _restoration;

  Map<String, Object?> workspaceManifestJson() {
    return {
      'protocolVersion': 4,
      'workspaceEpoch': _structuralWorkspaceEpoch,
      'workspaceRevision': _structuralWorkspaceRevision,
      'nextCreationSequence': _nextCreationSequence,
      'nextModificationSequence': _nextModificationSequence,
      'focusedSceneId': _presentedDocument?.descriptor.sceneId,
      'scenes': _workspaceDocuments.map((document) {
        final descriptor = document.descriptor;
        final creation = _creationSequences[descriptor.sceneId];
        if (creation == null) {
          throw StateError(
              'Canvas scene is missing durable creation sequence.');
        }
        return {
          'sceneId': descriptor.sceneId,
          'sceneRevision': descriptor.sceneRevision,
          'generationId': descriptor.generationId,
          'sourceTurnId': descriptor.sourceTurnId,
          'svgSha256': descriptor.svgSha256,
          'manifestSha256': descriptor.manifestSha256,
          'svgByteLength': descriptor.svgByteLength,
          'manifestByteLength': descriptor.manifestByteLength,
          'rendererCapabilityVersion': avaSvgRendererCapabilityVersion,
          'validatorVersion': avaSvgValidatorVersion,
          'manifestVersion': avaSvgManifestVersion,
          'creationSequence': creation,
          'modificationSequence': _modificationSequences[descriptor.sceneId],
          'title': document.manifest.title,
          'summary': document.manifest.description,
        };
      }).toList(growable: false),
    };
  }

  void updateRenderProfile(AvaCanvasRenderProfileV4 profile) {
    if (_sameRenderProfile(_renderProfile, profile)) return;
    _renderProfile = profile;
    if (_renderProfileUpdateSink == null) return;
    _renderProfileTimer?.cancel();
    _renderProfileTimer = Timer(
      _renderProfileDebounce,
      _flushRenderProfileUpdate,
    );
  }

  AvaCanvasRenderProfileV4 requireRenderProfile() {
    final profile = _renderProfile;
    if (profile == null) {
      throw StateError(
        'The canvas workspace must be measured before session creation.',
      );
    }
    return profile;
  }

  int? retainedSceneRevision(String sceneId) {
    for (final document in _workspaceDocuments) {
      if (document.descriptor.sceneId == sceneId) {
        return document.descriptor.sceneRevision;
      }
    }
    return null;
  }

  bool get panelVisible {
    return _controller != null && !_panelDismissed && _hasMeaningfulCanvasState;
  }

  bool get canReopen =>
      _controller != null && _panelDismissed && _hasMeaningfulCanvasState;

  bool get generating {
    final phase = _controller?.lifecyclePhase;
    return phase == AvaSvgCanvasLifecyclePhase.generating ||
        phase == AvaSvgCanvasLifecyclePhase.draftVisible ||
        phase == AvaSvgCanvasLifecyclePhase.validatedCandidate ||
        phase == AvaSvgCanvasLifecyclePhase.preparing ||
        phase == AvaSvgCanvasLifecyclePhase.presentationScheduled ||
        phase == AvaSvgCanvasLifecyclePhase.presented;
  }

  bool get focusTransitionPending => _focusTransitionPending;

  int get currentSceneIndex {
    final presented = _presentedDocument;
    if (presented == null) return -1;
    return _workspaceDocuments.indexWhere(
      (document) => document.descriptor.sceneId == presented.descriptor.sceneId,
    );
  }

  void requestPreviousScene() => _controller?.emitPresentationRequested(
        operation: 'show',
        selector: 'previous',
      );

  void requestNextScene() => _controller?.emitPresentationRequested(
        operation: 'show',
        selector: 'next',
      );

  Future<void> _applyPresentationScene(
    AvaValidatedSvgCanvasDocument target,
  ) async {
    if (identical(_presentedDocument, target) ||
        _presentedDocument?.descriptor.sceneId == target.descriptor.sceneId) {
      return;
    }
    if (_focusRevision == 1000000) {
      throw StateError('Canvas focus revision exhausted its protocol bound.');
    }
    final revision = ++_focusRevision;
    final epoch = _workspaceEpoch;
    final targetSceneId = target.descriptor.sceneId;
    _presentedDocument = target;
    _clearAcceptedPresentation();
    _controller?.focusRetainedActive(
      target,
      focusRevision: revision,
      pendingSync: _runtimeFocusSink != null,
    );
    _focusTransitionPending = true;
    _persistence
        ?.persistFocus(targetSceneId)
        .catchError((Object error, StackTrace stackTrace) {
      // Non-terminal local persistence error.
    });
    notifyListeners();
    try {
      final sink = _runtimeFocusSink;
      if (sink != null) {
        await sink(target, revision);
      }
      if (_workspaceEpoch == epoch && _focusRevision == revision) {
        _controller?.completeFocusSync(
          sceneId: targetSceneId,
          focusRevision: revision,
        );
      }
    } catch (error) {
      if (_workspaceEpoch == epoch && _focusRevision == revision) {
        _controller?.failFocusSync(
          sceneId: targetSceneId,
          focusRevision: revision,
          error: error,
        );
      }
      rethrow;
    } finally {
      if (_workspaceEpoch == epoch && _focusRevision == revision) {
        _focusTransitionPending = false;
        notifyListeners();
      }
    }
  }

  void attach(
    AvaSvgCanvasController controller, {
    AvaCanvasRenderProfileV4? initialRenderProfile,
    AvaCanvasRenderProfileUpdateSink? renderProfileUpdateSink,
    AvaCanvasRuntimeFocusSink? runtimeFocusSink,
  }) {
    if ((initialRenderProfile == null) != (renderProfileUpdateSink == null)) {
      throw ArgumentError(
        'An active canvas session requires both its initial render profile '
        'and update sink.',
      );
    }
    if (identical(_controller, controller)) {
      _runtimeFocusSink = runtimeFocusSink;
      return;
    }
    final previous = _controller;
    if (previous != null) {
      _retainPresentedDocument(previous);
    }
    final retainedActive = _presentedDocument;
    if (controller.active == null &&
        !controller.hasPendingVisual &&
        retainedActive != null) {
      controller.restoreRetainedActive(retainedActive);
    }
    _retainPresentedDocument(controller);
    previous?.removeListener(_handleControllerChanged);
    _renderProfileTimer?.cancel();
    _renderProfileTimer = null;
    _controller = controller;
    controller.currentFocusRevision = () => _focusRevision;
    _runtimeFocusSink = runtimeFocusSink;
    _renderProfileUpdateSink = renderProfileUpdateSink;
    _lastSentRenderProfile = initialRenderProfile;
    _renderProfileRevision = 0;
    _observedPendingGenerationId = null;
    _clearAcceptedPresentation();
    controller.addListener(_handleControllerChanged);
    controller.presentationCommandHandler = handlePresentationCommand;
    controller.presentationChangedHandler = handlePresentationChanged;
    _observePendingGeneration(controller);
    _clearDismissalWithoutCanvasState();
    if (renderProfileUpdateSink != null &&
        !_sameRenderProfile(_renderProfile, initialRenderProfile)) {
      _renderProfileTimer = Timer(
        _renderProfileDebounce,
        _flushRenderProfileUpdate,
      );
    }
    notifyListeners();
  }

  void detach(AvaSvgCanvasController controller) {
    if (!identical(_controller, controller)) return;
    _retainPresentedDocument(controller);
    controller.removeListener(_handleControllerChanged);
    controller.presentationCommandHandler = null;
    controller.presentationChangedHandler = null;
    _renderProfileTimer?.cancel();
    _renderProfileTimer = null;
    _renderProfileUpdateSink = null;
    _runtimeFocusSink = null;
    _lastSentRenderProfile = null;
    _renderProfileRevision = 0;
    controller.currentFocusRevision = null;
    _panelDismissed = true;
    _dismissedGenerationId = _displayGenerationId;
    _clearAcceptedPresentation();
    controller.markPresentationUnavailable();
    _controller = null;
    _observedPendingGenerationId = null;
    _clearDismissalWithoutCanvasState();
    notifyListeners();
  }

  void dismissPanel() {
    if (!panelVisible) return;
    _controller?.emitPresentationRequested(
      operation: 'hide',
    );
  }

  void reopenPanel() {
    if (!canReopen) return;
    _controller?.emitPresentationRequested(
      operation: 'show',
      selector: 'current',
    );
  }

  Future<void> handlePresentationCommand({
    required String commandId,
    required String action,
    String? sceneId,
  }) async {
    if (action == 'hide') {
      _panelDismissed = true;
      _dismissedGenerationId = _displayGenerationId;
      _clearAcceptedPresentation();
      _controller?.markPresentationUnavailable();
      notifyListeners();
      _controller?.emitPresentationAcknowledged(
        commandId: commandId,
        applied: true,
      );
      return;
    }

    if (action == 'show') {
      if (sceneId == null) {
        _controller?.emitPresentationAcknowledged(
          commandId: commandId,
          applied: false,
          reason: 'sceneNotFound',
        );
        return;
      }
      final target = _workspaceDocuments
          .where((doc) => doc.descriptor.sceneId == sceneId)
          .firstOrNull;
      if (target == null) {
        _controller?.emitPresentationAcknowledged(
          commandId: commandId,
          applied: false,
          reason: 'sceneNotFound',
        );
        return;
      }

      _panelDismissed = false;
      _dismissedGenerationId = null;

      if (_presentedDocument?.descriptor.sceneId == target.descriptor.sceneId) {
        if (_focusRevision == 0) {
          _focusRevision = 1;
        }
        notifyListeners();
        _controller?.emitPresentationAcknowledged(
          commandId: commandId,
          applied: true,
          sceneId: target.descriptor.sceneId,
          focusRevision: _focusRevision,
        );
        return;
      }

      final targetFocusRevision = _focusRevision + 1;
      try {
        await _applyPresentationScene(target);
        if (_presentedDocument?.descriptor.sceneId !=
                target.descriptor.sceneId ||
            _focusRevision != targetFocusRevision ||
            _controller?.activeFocusRevision != targetFocusRevision) {
          _controller?.emitPresentationAcknowledged(
            commandId: commandId,
            applied: false,
            reason: 'presentationApplyFailed',
          );
          return;
        }
        _controller?.emitPresentationAcknowledged(
          commandId: commandId,
          applied: true,
          sceneId: target.descriptor.sceneId,
          focusRevision: _focusRevision,
        );
      } catch (error) {
        _controller?.emitPresentationAcknowledged(
          commandId: commandId,
          applied: false,
          reason: 'clientSyncFailed',
        );
      }
    }
  }

  void handlePresentationChanged(Map<String, Object?> event) {
    final revision = event['revision'] as int? ?? 0;
    final visible = event['visible'] as bool? ?? false;
    final sceneId = event['sceneId'] as String?;
    final focusRevision = event['focusRevision'] as int?;

    if (revision <= _lastAppliedCanonicalRevision) {
      return;
    }

    _lastAppliedCanonicalRevision = revision;
    if (visible != panelVisible) {
      if (visible) {
        _panelDismissed = false;
        _dismissedGenerationId = null;
      } else {
        _panelDismissed = true;
      }
      notifyListeners();
    }
    _recordAcceptedPresentation(
      visible: visible,
      sceneId: sceneId,
      focusRevision: focusRevision,
    );
  }

  void clearPresentedFocus() {
    if (_presentedDocument == null &&
        !_panelDismissed &&
        _dismissedGenerationId == null &&
        _observedPendingGenerationId == null) {
      return;
    }
    _presentedDocument = null;
    _panelDismissed = false;
    _dismissedGenerationId = null;
    _observedPendingGenerationId = null;
    notifyListeners();
  }

  void clearWorkspace() {
    if (_controller != null) {
      throw StateError(
        'Canvas workspace can be cleared only after the realtime '
        'controller is detached.',
      );
    }
    _restoreInvalidated = true;
    _workspaceEpoch += 1;
    _structuralWorkspaceEpoch += 1;
    _structuralWorkspaceRevision = 0;
    _nextCreationSequence = 1;
    _nextModificationSequence = 1;
    _creationSequences.clear();
    _modificationSequences.clear();
    _lastAppliedCanonicalRevision = 0;
    _clearAcceptedPresentation();
    if (_workspaceDocuments.isEmpty &&
        _presentedDocument == null &&
        !_panelDismissed &&
        _dismissedGenerationId == null &&
        _observedPendingGenerationId == null) {
      return;
    }
    _workspaceDocuments.clear();
    _presentedDocument = null;
    _panelDismissed = false;
    _dismissedGenerationId = null;
    _observedPendingGenerationId = null;
    notifyListeners();
  }

  bool _recordAcceptedPresentation({
    required bool visible,
    required String? sceneId,
    required int? focusRevision,
  }) {
    if (!visible) {
      _clearAcceptedPresentation();
      _controller?.markPresentationUnavailable();
      return !panelVisible;
    }
    if (sceneId == null || focusRevision == null) return false;
    if (_presentedDocument?.descriptor.sceneId != sceneId ||
        _focusRevision > focusRevision) {
      return false;
    }
    _focusRevision = focusRevision;
    _acceptPresentation(sceneId: sceneId, focusRevision: focusRevision);
    return true;
  }

  void _acceptPresentation({
    required String sceneId,
    required int focusRevision,
  }) {
    _acceptedPresentationVisible = true;
    _acceptedPresentationSceneId = sceneId;
    _acceptedPresentationFocusRevision = focusRevision;
    _controller?.acceptPresentedScene(
      sceneId: sceneId,
      focusRevision: focusRevision,
    );
  }

  void _clearAcceptedPresentation() {
    _acceptedPresentationVisible = false;
    _acceptedPresentationSceneId = null;
    _acceptedPresentationFocusRevision = null;
  }

  void _handleControllerChanged() {
    final controller = _controller;
    if (controller == null) return;
    final completed = controller.retainedCompletion;
    if (completed != null) {
      controller.retainedCompletion = null;
      final mutation = controller.structuralMutationCompletion;
      controller.structuralMutationCompletion = null;
      if (mutation == null) {
        throw StateError('Canvas activation omitted its structural mutation.');
      }
      final foreground = identical(controller.active?.document, completed);
      _retainDocument(
        completed,
        foreground: foreground,
        mutation: mutation,
      );
      if (foreground) {
        _focusRevision = controller.activeFocusRevision;
        _acceptPresentation(
          sceneId: completed.descriptor.sceneId,
          focusRevision: controller.activeFocusRevision,
        );
      }
    }
    _retainPresentedDocument(controller);
    _observePendingGeneration(controller);
    _clearDismissalWithoutCanvasState();
    notifyListeners();
  }

  bool get _hasMeaningfulCanvasState =>
      _presentedDocument != null || (_controller?.hasPendingVisual ?? false);

  String? get _displayGenerationId =>
      _pendingGenerationId(_controller) ??
      _controller?.active?.document.descriptor.generationId ??
      _presentedDocument?.descriptor.generationId;

  void _observePendingGeneration(AvaSvgCanvasController controller) {
    final generationId = _pendingGenerationId(controller);
    if (generationId == null) {
      _observedPendingGenerationId = null;
      return;
    }
    if (_observedPendingGenerationId == generationId) return;
    _observedPendingGenerationId = generationId;
    if (_panelDismissed && _dismissedGenerationId != generationId) {
      _panelDismissed = false;
      _dismissedGenerationId = null;
    }
  }

  void _clearDismissalWithoutCanvasState() {
    if (_hasMeaningfulCanvasState) return;
    _panelDismissed = false;
    _dismissedGenerationId = null;
  }

  String? _pendingGenerationId(AvaSvgCanvasController? controller) =>
      controller?.candidate?.descriptor.generationId ??
      controller?.draft?.document.descriptor.generationId ??
      controller?.generation?.generationId;

  void _retainPresentedDocument(AvaSvgCanvasController controller) {
    final active = controller.active?.document;
    if (active == null) return;
    _retainDocument(active, foreground: true);
  }

  void _retainDocument(
    AvaValidatedSvgCanvasDocument active, {
    required bool foreground,
    ({
      int workspaceEpoch,
      int workspaceRevision,
      int creationSequence,
      int modificationSequence,
      String? evictedSceneId,
    })? mutation,
  }) {
    final descriptor = active.descriptor;
    final index = _workspaceDocuments.indexWhere(
      (document) => document.descriptor.sceneId == descriptor.sceneId,
    );
    if (index < 0) {
      if (descriptor.sceneRevision != 1) {
        throw StateError(
          'A new canvas scene must enter the workspace at revision 1.',
        );
      }
      if (mutation != null) {
        _validateStructuralMutation(mutation);
      }
      final expectedEvicted = _workspaceDocuments.length == maximumSceneCount
          ? _workspaceDocuments.first.descriptor.sceneId
          : null;
      if (_persistence is AvaStructuralCanvasPersistence &&
          mutation != null &&
          mutation.evictedSceneId != expectedEvicted) {
        throw StateError(
            'Canvas structural mutation reported an unexpected eviction.');
      }
      if (expectedEvicted != null) {
        _workspaceDocuments.removeAt(0);
        _creationSequences.remove(expectedEvicted);
        _modificationSequences.remove(expectedEvicted);
      }
      _workspaceDocuments.add(active);
      final creationSequence =
          mutation?.creationSequence ?? _nextCreationSequence;
      final modificationSequence = mutation?.modificationSequence;
      _creationSequences[descriptor.sceneId] = creationSequence;
      _modificationSequences[descriptor.sceneId] = modificationSequence;
      if (mutation != null) _acceptStructuralMutation(mutation);
      if (foreground) _presentedDocument = active;
      _persistActive(active, foreground: foreground, mutation: mutation);
      return;
    }

    final retained = _workspaceDocuments[index];
    final retainedRevision = retained.descriptor.sceneRevision;
    if (descriptor.sceneRevision < retainedRevision) {
      throw StateError(
        'A stale canvas scene revision cannot replace the retained revision.',
      );
    }
    if (descriptor.sceneRevision == retainedRevision) {
      if (!_hasSameDocumentIdentity(retained, active)) {
        throw StateError(
          'A canvas scene revision cannot change its validated identity.',
        );
      }
      return;
    }
    if (mutation != null && _persistence is AvaStructuralCanvasPersistence) {
      _validateStructuralMutation(mutation);
      if (mutation.evictedSceneId != null ||
          mutation.creationSequence != _creationSequences[descriptor.sceneId]) {
        throw StateError(
            'Canvas revision changed creation order or evicted a scene.');
      }
    }
    _workspaceDocuments[index] = active;
    if (mutation != null) {
      _modificationSequences[descriptor.sceneId] =
          mutation.modificationSequence;
      _acceptStructuralMutation(mutation);
    }
    if (foreground) _presentedDocument = active;
    _persistActive(active, foreground: foreground, mutation: mutation);
  }

  void _validateStructuralMutation(
      ({
        int workspaceEpoch,
        int workspaceRevision,
        int creationSequence,
        int modificationSequence,
        String? evictedSceneId,
      }) mutation) {
    if (_persistence is! AvaStructuralCanvasPersistence) return;
    if (mutation.workspaceEpoch != _structuralWorkspaceEpoch ||
        mutation.workspaceRevision != _structuralWorkspaceRevision + 1) {
      throw StateError(
          'Canvas structural mutation is stale or has a revision gap.');
    }
  }

  void _acceptStructuralMutation(
      ({
        int workspaceEpoch,
        int workspaceRevision,
        int creationSequence,
        int modificationSequence,
        String? evictedSceneId,
      }) mutation) {
    if (mutation.workspaceRevision > _structuralWorkspaceRevision) {
      _structuralWorkspaceRevision = mutation.workspaceRevision;
    }
    if (_nextCreationSequence <= mutation.creationSequence) {
      _nextCreationSequence = mutation.creationSequence + 1;
    }
    if (_nextModificationSequence <= mutation.modificationSequence) {
      _nextModificationSequence = mutation.modificationSequence + 1;
    }
  }

  Future<void> _restorePersistedWorkspace() async {
    final persistence = _persistence;
    if (persistence == null) return;
    try {
      final restored =
          await persistence.loadWorkspace().timeout(_persistenceRestoreTimeout);
      if (_restoreInvalidated) return;
      if (restored.documents.length > maximumSceneCount) {
        throw StateError('Canvas persistence exceeded the workspace bound.');
      }
      final sceneIds = <String>{};
      for (final document in restored.documents) {
        if (!document.descriptor.isLocallyRestored ||
            !sceneIds.add(document.descriptor.sceneId)) {
          throw StateError(
              'Canvas persistence returned invalid workspace state.');
        }
      }
      final focusedSceneId = restored.focusedSceneId;
      final focused = focusedSceneId == null
          ? null
          : restored.documents
              .where(
                (document) => document.descriptor.sceneId == focusedSceneId,
              )
              .firstOrNull;
      if (focusedSceneId != null && focused == null) {
        throw StateError('Canvas persistence returned an unknown focus.');
      }
      _workspaceDocuments
        ..clear()
        ..addAll(restored.documents);
      _structuralWorkspaceEpoch = restored.workspaceEpoch;
      _structuralWorkspaceRevision = restored.workspaceRevision;
      _creationSequences.clear();
      _modificationSequences.clear();
      for (var index = 0; index < restored.documents.length; index++) {
        final sceneId = restored.documents[index].descriptor.sceneId;
        _creationSequences[sceneId] =
            restored.creationSequences[sceneId] ?? index + 1;
        _modificationSequences[sceneId] =
            restored.modificationSequences[sceneId];
      }
      final creationHighWater = _creationSequences.values.fold<int>(
        0,
        (current, value) => value > current ? value : current,
      );
      final knownModifications = _modificationSequences.values.whereType<int>();
      final modificationHighWater = knownModifications.fold<int>(
        0,
        (current, value) => value > current ? value : current,
      );
      _nextCreationSequence = restored.nextCreationSequence > creationHighWater
          ? restored.nextCreationSequence
          : creationHighWater + 1;
      _nextModificationSequence =
          restored.nextModificationSequence > modificationHighWater
              ? restored.nextModificationSequence
              : modificationHighWater + 1;
      _presentedDocument = focused;
      if (focused != null) {
        _panelDismissed = true;
        _dismissedGenerationId = focused.descriptor.generationId;
      }
      notifyListeners();
    } on Object catch (error, stackTrace) {
      developer.log(
        'canvas_restore_failed failureType=${error.runtimeType}',
        name: 'ava.canvas.persistence',
        error: error,
        stackTrace: stackTrace,
      );
      _workspaceDocuments.clear();
      _presentedDocument = null;
      _creationSequences.clear();
      _modificationSequences.clear();
    }
  }

  void _persistActive(
    AvaValidatedSvgCanvasDocument document, {
    bool foreground = true,
    ({
      int workspaceEpoch,
      int workspaceRevision,
      int creationSequence,
      int modificationSequence,
      String? evictedSceneId,
    })? mutation,
  }) {
    final persistence = _persistence;
    if (persistence == null || document.descriptor.isLocallyRestored) return;
    final operation =
        persistence is AvaStructuralCanvasPersistence && mutation != null
            ? persistence.persistStructuralActive(
                document,
                focused: foreground,
                workspaceEpoch: mutation.workspaceEpoch,
                workspaceRevision: mutation.workspaceRevision,
                creationSequence: mutation.creationSequence,
                modificationSequence: mutation.modificationSequence,
                evictedSceneId: mutation.evictedSceneId,
              )
            : persistence.persistActive(document, focused: foreground);
    unawaited(
      operation.onError((error, stackTrace) {
        final descriptor = document.descriptor;
        developer.log(
          'canvas_persist_failed sceneId=${descriptor.sceneId} '
          'sceneRevision=${descriptor.sceneRevision} '
          'failureType=${error.runtimeType}',
          name: 'ava.canvas.persistence',
          error: error,
          stackTrace: stackTrace,
        );
      }),
    );
  }

  bool _hasSameDocumentIdentity(
    AvaValidatedSvgCanvasDocument retained,
    AvaValidatedSvgCanvasDocument observed,
  ) {
    final left = retained.descriptor;
    final right = observed.descriptor;
    return left.generationId == right.generationId &&
        left.sourceTurnId == right.sourceTurnId &&
        left.sceneId == right.sceneId &&
        left.sceneRevision == right.sceneRevision &&
        left.svgSha256 == right.svgSha256 &&
        left.manifestSha256 == right.manifestSha256 &&
        left.svgByteLength == right.svgByteLength &&
        left.manifestByteLength == right.manifestByteLength;
  }

  void _flushRenderProfileUpdate() {
    _renderProfileTimer = null;
    final sink = _renderProfileUpdateSink;
    final profile = _renderProfile;
    if (sink == null ||
        profile == null ||
        _sameRenderProfile(profile, _lastSentRenderProfile)) {
      return;
    }
    if (_renderProfileRevision == 1000000) {
      throw StateError('Canvas render-profile revision exhausted its bound.');
    }
    final revision = _renderProfileRevision + 1;
    sink(revision, profile);
    _renderProfileRevision = revision;
    _lastSentRenderProfile = profile;
  }

  @override
  void dispose() {
    _renderProfileTimer?.cancel();
    _renderProfileTimer = null;
    _renderProfileUpdateSink = null;
    _controller?.removeListener(_handleControllerChanged);
    _controller = null;
    _workspaceDocuments.clear();
    _creationSequences.clear();
    _modificationSequences.clear();
    _presentedDocument = null;
    _panelDismissed = false;
    _dismissedGenerationId = null;
    _observedPendingGenerationId = null;
    super.dispose();
  }
}

bool _sameRenderProfile(
  AvaCanvasRenderProfileV4? left,
  AvaCanvasRenderProfileV4? right,
) =>
    identical(left, right) ||
    (left != null &&
        right != null &&
        left.usableAspectRatio == right.usableAspectRatio &&
        left.orientation == right.orientation &&
        left.widthClass == right.widthClass &&
        left.heightClass == right.heightClass &&
        left.safeInsets.top == right.safeInsets.top &&
        left.safeInsets.right == right.safeInsets.right &&
        left.safeInsets.bottom == right.safeInsets.bottom &&
        left.safeInsets.left == right.safeInsets.left &&
        left.touchEnvironment == right.touchEnvironment &&
        left.minimumInteractionTargetPoints ==
            right.minimumInteractionTargetPoints &&
        left.textScaleFactor == right.textScaleFactor);
