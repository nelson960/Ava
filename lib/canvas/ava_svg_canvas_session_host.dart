import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/ava_svg_canvas_protocol.dart';
import '../models/ava_realtime_protocol_v4.dart';
import 'ava_svg_canvas_controller.dart';

typedef AvaCanvasRenderProfileUpdateSink = void Function(
  int profileRevision,
  AvaCanvasRenderProfileV4 renderProfile,
);

/// Application-level owner of the presented SVG and the controller attached
/// to the current realtime session.
final class AvaSvgCanvasSessionHost extends ChangeNotifier {
  static const int maximumSceneCount = 10;

  AvaSvgCanvasSessionHost({
    Duration renderProfileDebounce = const Duration(milliseconds: 150),
  }) : _renderProfileDebounce = renderProfileDebounce {
    if (renderProfileDebounce.isNegative) {
      throw ArgumentError.value(
        renderProfileDebounce,
        'renderProfileDebounce',
        'cannot be negative',
      );
    }
  }

  final Duration _renderProfileDebounce;
  AvaSvgCanvasController? _controller;
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
    if (_panelDismissed) return false;
    return _hasMeaningfulCanvasState;
  }

  bool get canReopen => _panelDismissed && _hasMeaningfulCanvasState;

  bool get generating {
    final phase = _controller?.lifecyclePhase;
    return phase == AvaSvgCanvasLifecyclePhase.generating ||
        phase == AvaSvgCanvasLifecyclePhase.draftVisible ||
        phase == AvaSvgCanvasLifecyclePhase.validatedCandidate ||
        phase == AvaSvgCanvasLifecyclePhase.preparing ||
        phase == AvaSvgCanvasLifecyclePhase.presentationScheduled ||
        phase == AvaSvgCanvasLifecyclePhase.presented;
  }

  void attach(
    AvaSvgCanvasController controller, {
    AvaCanvasRenderProfileV4? initialRenderProfile,
    AvaCanvasRenderProfileUpdateSink? renderProfileUpdateSink,
  }) {
    if ((initialRenderProfile == null) != (renderProfileUpdateSink == null)) {
      throw ArgumentError(
        'An active canvas session requires both its initial render profile '
        'and update sink.',
      );
    }
    if (identical(_controller, controller)) return;
    final previous = _controller;
    if (previous != null) {
      _retainPresentedDocument(previous);
    }
    _retainPresentedDocument(controller);
    previous?.removeListener(_handleControllerChanged);
    _renderProfileTimer?.cancel();
    _renderProfileTimer = null;
    _controller = controller;
    _renderProfileUpdateSink = renderProfileUpdateSink;
    _lastSentRenderProfile = initialRenderProfile;
    _renderProfileRevision = 0;
    _observedPendingGenerationId = null;
    controller.addListener(_handleControllerChanged);
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
    _renderProfileTimer?.cancel();
    _renderProfileTimer = null;
    _renderProfileUpdateSink = null;
    _lastSentRenderProfile = null;
    _renderProfileRevision = 0;
    _controller = null;
    _observedPendingGenerationId = null;
    _clearDismissalWithoutCanvasState();
    notifyListeners();
  }

  void dismissPanel() {
    if (!panelVisible) return;
    final generationId = _displayGenerationId;
    if (generationId == null) return;
    _panelDismissed = true;
    _dismissedGenerationId = generationId;
    notifyListeners();
  }

  void reopenPanel() {
    if (!canReopen) return;
    _panelDismissed = false;
    _dismissedGenerationId = null;
    notifyListeners();
  }

  void clearWorkspace() {
    if (_controller != null) {
      throw StateError(
        'Canvas workspace can be cleared only after the realtime '
        'controller is detached.',
      );
    }
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

  void _handleControllerChanged() {
    final controller = _controller;
    if (controller == null) return;
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
      if (_workspaceDocuments.length == maximumSceneCount) {
        _workspaceDocuments.removeAt(0);
      }
      _workspaceDocuments.add(active);
      _presentedDocument = active;
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
    _workspaceDocuments[index] = active;
    _presentedDocument = active;
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
        left.svgPath == right.svgPath &&
        left.manifestPath == right.manifestPath &&
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
