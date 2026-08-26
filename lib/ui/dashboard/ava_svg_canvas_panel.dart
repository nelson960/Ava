import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../../canvas/ava_svg_canvas_controller.dart';
import '../../canvas/ava_svg_canvas_session_host.dart';
import '../../models/ava_svg_canvas_protocol.dart';
import '../design/ava_design.dart';
import 'ava_svg_canvas_view.dart';

final class AvaSvgCanvasPanel extends StatelessWidget {
  const AvaSvgCanvasPanel({
    super.key,
    required this.host,
  });

  final AvaSvgCanvasSessionHost host;

  @override
  Widget build(BuildContext context) {
    final controller = host.controller;
    final presented = host.presentedDocument;
    final liveActive = controller?.active?.document;
    final candidate = controller?.candidate?.document;
    final documents = <AvaValidatedSvgCanvasDocument>[
      if (presented != null) presented,
      if (candidate != null &&
          candidate.descriptor.generationId !=
              presented?.descriptor.generationId)
        candidate,
    ];
    if (documents.isEmpty) return const SizedBox.shrink();

    return Material(
      color: const Color(0xFFF8FBFB),
      elevation: 18,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        fit: StackFit.expand,
        children: [
          for (final document in documents)
            _documentLayer(
              controller: controller,
              document: document,
              interactive: identical(document, liveActive) &&
                  (controller?.activeRendererHealthy ?? false),
              candidate: identical(document, candidate),
            ),
          Positioned(
            top: 8,
            right: 8,
            child: SafeArea(
              bottom: false,
              child: Semantics(
                button: true,
                label: 'Close drawing',
                child: IconButton(
                  tooltip: 'Close drawing',
                  onPressed: host.dismissPanel,
                  icon: const Icon(CupertinoIcons.xmark_circle_fill),
                  color: AvaColors.ink.withValues(alpha: 0.72),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _documentLayer({
    required AvaSvgCanvasController? controller,
    required AvaValidatedSvgCanvasDocument document,
    required bool interactive,
    required bool candidate,
  }) {
    final generationId = document.descriptor.generationId;
    AvaSvgCanvasController? liveController() {
      if (controller == null || !identical(host.controller, controller)) {
        return null;
      }
      return controller;
    }

    return KeyedSubtree(
      key: ValueKey('svg-canvas-$generationId'),
      child: IgnorePointer(
        ignoring: !interactive,
        child: AvaSvgCanvasView(
          document: document,
          present:
              !candidate || (liveController()?.shouldPresentCandidate ?? false),
          onPrepared: () {
            final ownedController = liveController();
            if (ownedController == null) return;
            final state = ownedController.candidate;
            if (state?.descriptor.generationId == generationId &&
                state?.phase == AvaSvgCanvasCandidatePhase.rendererPreparing) {
              ownedController.rendererPrepared();
            }
          },
          onFirstFramePresented: () {
            final ownedController = liveController();
            if (!candidate || ownedController == null) return;
            final state = ownedController.candidate;
            if (state?.descriptor.generationId == generationId &&
                state?.phase ==
                    AvaSvgCanvasCandidatePhase.presentationRequested) {
              ownedController.firstFramePresented();
            }
          },
          onBinding: (binding) {
            final ownedController = liveController();
            if (interactive && ownedController != null) {
              ownedController.reportBinding(binding);
            }
          },
          onError: (message) {
            final ownedController = liveController();
            if ((!candidate && !interactive) || ownedController == null) {
              return;
            }
            unawaited(
              ownedController.rendererFailed(
                message,
                generationId: generationId,
              ),
            );
          },
        ),
      ),
    );
  }
}
