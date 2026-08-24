import 'package:flutter/foundation.dart';

import 'ava_svg_canvas_controller.dart';

/// Stable UI owner for the SVG controller attached to the current realtime
/// session. A new authenticated session always replaces the previous scene.
final class AvaSvgCanvasSessionHost extends ChangeNotifier {
  AvaSvgCanvasController? _controller;
  bool _panelDismissed = false;
  String? _lastPresentationGeneration;

  AvaSvgCanvasController? get controller => _controller;
  bool get panelDismissed => _panelDismissed;

  bool get panelVisible {
    final controller = _controller;
    if (controller == null || _panelDismissed) return false;
    return controller.active != null || controller.shouldPresentCandidate;
  }

  bool get canReopen =>
      _panelDismissed && _controller?.active?.document != null;

  bool get generating {
    final candidate = _controller?.candidate;
    return _controller?.generation != null ||
        (candidate != null && !controller!.shouldPresentCandidate);
  }

  void attach(AvaSvgCanvasController controller) {
    if (identical(_controller, controller)) return;
    final previous = _controller;
    if (previous != null) previous.removeListener(_handleControllerChanged);
    _controller = controller;
    _panelDismissed = false;
    _lastPresentationGeneration = null;
    controller.addListener(_handleControllerChanged);
    notifyListeners();
  }

  void detach(AvaSvgCanvasController controller) {
    if (!identical(_controller, controller)) return;
    controller.removeListener(_handleControllerChanged);
    _controller = null;
    _panelDismissed = false;
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
    final candidate = _controller?.candidate;
    if (candidate != null && _controller!.shouldPresentCandidate) {
      final generation = candidate.descriptor.generationId;
      if (_lastPresentationGeneration != generation) {
        _lastPresentationGeneration = generation;
        _panelDismissed = false;
      }
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _controller?.removeListener(_handleControllerChanged);
    _controller = null;
    super.dispose();
  }
}
