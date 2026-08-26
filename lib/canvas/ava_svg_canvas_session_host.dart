import 'package:flutter/foundation.dart';

import '../models/ava_svg_canvas_protocol.dart';
import 'ava_svg_canvas_controller.dart';

/// Application-level owner of the presented SVG and the controller attached
/// to the current realtime session.
final class AvaSvgCanvasSessionHost extends ChangeNotifier {
  AvaSvgCanvasController? _controller;
  AvaValidatedSvgCanvasDocument? _presentedDocument;
  bool _panelDismissed = false;
  String? _lastPresentationGeneration;

  AvaSvgCanvasController? get controller => _controller;
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
      previous.removeListener(_handleControllerChanged);
    }
    _controller = controller;
    _lastPresentationGeneration = null;
    _retainPresentedDocument(controller);
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
    if (active != null) _presentedDocument = active;
  }

  @override
  void dispose() {
    _controller?.removeListener(_handleControllerChanged);
    _controller = null;
    _presentedDocument = null;
    super.dispose();
  }
}
