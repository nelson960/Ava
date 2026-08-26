import 'package:flutter/foundation.dart';

import '../models/ava_svg_canvas_protocol.dart';
import 'ava_svg_canvas_controller.dart';

/// Application-level owner of the presented SVG and the controller attached
/// to the current realtime session.
final class AvaSvgCanvasSessionHost extends ChangeNotifier {
  static const int maximumSceneCount = 10;

  AvaSvgCanvasController? _controller;
  final List<AvaValidatedSvgCanvasDocument> _workspaceDocuments = [];
  AvaValidatedSvgCanvasDocument? _presentedDocument;
  bool _panelDismissed = false;
  String? _lastPresentationGeneration;

  AvaSvgCanvasController? get controller => _controller;
  List<AvaValidatedSvgCanvasDocument> get workspaceDocuments =>
      List.unmodifiable(_workspaceDocuments);
  int get sceneCount => _workspaceDocuments.length;
  AvaValidatedSvgCanvasDocument? get presentedDocument => _presentedDocument;
  bool get panelDismissed => _panelDismissed;

  bool get panelVisible {
    if (_panelDismissed) return false;
    return _presentedDocument != null ||
        _controller?.shouldPresentCandidate == true;
  }

  bool get canReopen => _panelDismissed && _presentedDocument != null;

  bool get generating {
    final candidate = _controller?.candidate;
    return _controller?.generation != null ||
        (candidate != null && !controller!.shouldPresentCandidate);
  }

  void attach(AvaSvgCanvasController controller) {
    if (identical(_controller, controller)) return;
    final previous = _controller;
    if (previous != null) {
      _retainPresentedDocument(previous);
    }
    _retainPresentedDocument(controller);
    previous?.removeListener(_handleControllerChanged);
    _controller = controller;
    _lastPresentationGeneration = null;
    controller.addListener(_handleControllerChanged);
    notifyListeners();
  }

  void detach(AvaSvgCanvasController controller) {
    if (!identical(_controller, controller)) return;
    _retainPresentedDocument(controller);
    controller.removeListener(_handleControllerChanged);
    _controller = null;
    _lastPresentationGeneration = null;
    notifyListeners();
  }

  void dismissPanel() {
    if (!panelVisible) return;
    _panelDismissed = true;
    notifyListeners();
  }

  void reopenPanel() {
    if (!canReopen) return;
    _panelDismissed = false;
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
        _lastPresentationGeneration == null) {
      return;
    }
    _workspaceDocuments.clear();
    _presentedDocument = null;
    _panelDismissed = false;
    _lastPresentationGeneration = null;
    notifyListeners();
  }

  void _handleControllerChanged() {
    final controller = _controller;
    if (controller == null) return;
    _retainPresentedDocument(controller);
    final candidate = controller.candidate;
    if (candidate != null && controller.shouldPresentCandidate) {
      final generation = candidate.descriptor.generationId;
      if (_lastPresentationGeneration != generation) {
        _lastPresentationGeneration = generation;
        _panelDismissed = false;
      }
    }
    notifyListeners();
  }

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

  @override
  void dispose() {
    _controller?.removeListener(_handleControllerChanged);
    _controller = null;
    _workspaceDocuments.clear();
    _presentedDocument = null;
    super.dispose();
  }
}
