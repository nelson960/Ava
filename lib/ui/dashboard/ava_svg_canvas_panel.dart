import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../../canvas/ava_svg_canvas_controller.dart';
import '../../canvas/ava_svg_canvas_session_host.dart';
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
    final liveActive = controller?.active?.document;
    final active = liveActive ?? host.presentedDocument;
    final draft = controller?.draft?.document;
    final candidate = controller?.candidate?.document;
    final hasPending = controller?.hasPendingVisual ?? false;
    if (active == null && !hasPending) return const SizedBox.shrink();

    final interactionsEnabled = liveActive != null &&
        !hasPending &&
        (controller?.activeRendererHealthy ?? false);

    AvaSvgCanvasController? liveController() {
      if (controller == null || !identical(host.controller, controller)) {
        return null;
      }
      return controller;
    }

    return Material(
      color: const Color(0xFFF8FBFB),
      elevation: 18,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        fit: StackFit.expand,
        children: [
          IgnorePointer(
            ignoring: !interactionsEnabled,
            child: AvaSvgCanvasView(
              activeDocument: active,
              draft: draft,
              candidate: candidate,
              presentCandidate:
                  liveController()?.shouldPresentCandidate ?? false,
              interactionsEnabled: interactionsEnabled,
              onDraftPresented: (presentedDraft) {
                final ownedController = liveController();
                if (ownedController?.draft?.document == presentedDraft) {
                  ownedController!.draftPresented(presentedDraft);
                }
              },
              onCandidatePrepared: (preparedDocument) {
                final ownedController = liveController();
                final state = ownedController?.candidate;
                if (identical(state?.document, preparedDocument) &&
                    state?.phase ==
                        AvaSvgCanvasCandidatePhase.rendererPreparing) {
                  ownedController!.rendererPrepared();
                }
              },
              onCandidateFirstFramePresented: (presentedDocument) {
                final ownedController = liveController();
                final state = ownedController?.candidate;
                if (identical(state?.document, presentedDocument) &&
                    state?.phase ==
                        AvaSvgCanvasCandidatePhase.presentationRequested) {
                  ownedController!.firstFramePresented();
                }
              },
              onBinding: (binding) {
                final ownedController = liveController();
                if (interactionsEnabled && ownedController != null) {
                  ownedController.reportBinding(binding);
                }
              },
              onError: (message, {generationId}) {
                final ownedController = liveController();
                if (ownedController == null) return;
                final targetGenerationId = generationId ??
                    ownedController.candidate?.descriptor.generationId ??
                    ownedController.draft?.document.descriptor.generationId ??
                    ownedController.active?.document.descriptor.generationId;
                unawaited(
                  ownedController.rendererFailed(
                    message,
                    generationId: targetGenerationId,
                  ),
                );
              },
            ),
          ),
          if (hasPending)
            Positioned(
              left: 16,
              top: 12,
              child: IgnorePointer(
                child: _CanvasWorkingLabel(
                  previewVisible: draft != null || candidate != null,
                ),
              ),
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
}

final class _CanvasWorkingLabel extends StatelessWidget {
  const _CanvasWorkingLabel({required this.previewVisible});

  final bool previewVisible;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      label: previewVisible
          ? 'Ava is finishing this drawing'
          : 'Ava is drawing a visual',
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.9),
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: AvaColors.line),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const SizedBox.square(
                dimension: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AvaColors.teal,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                previewVisible ? 'Finishing drawing…' : 'Drawing…',
                style: const TextStyle(
                  color: AvaColors.ink,
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
