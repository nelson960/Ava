import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../../canvas/ava_svg_canvas_controller.dart';
import '../../canvas/ava_svg_canvas_session_host.dart';
import '../../services/ava_canvas_save_service.dart';
import '../../services/ava_canvas_share_service.dart';
import '../design/ava_design.dart';
import 'ava_svg_canvas_view.dart';

final class AvaSvgCanvasPanel extends StatelessWidget {
  const AvaSvgCanvasPanel({
    super.key,
    required this.host,
    this.shareService = const IoAvaCanvasShareService(),
    this.saveService = const IoAvaCanvasSaveService(),
  });

  final AvaSvgCanvasSessionHost host;
  final AvaCanvasShareService shareService;
  final AvaCanvasSaveService saveService;

  @override
  Widget build(BuildContext context) {
    final controller = host.controller;
    final liveActive = controller?.active?.document;
    final hasPending = controller?.hasPendingVisual ?? false;
    final isPendingNew =
        hasPending && controller?.generation?.operation == 'new';
    final active = isPendingNew ? null : (liveActive ?? host.presentedDocument);
    final draft = controller?.draft?.document;
    final candidate = controller?.candidate?.document;
    if (active == null && !hasPending) return const SizedBox.shrink();

    final isMaskActive = isPendingNew && draft == null && candidate == null;

    final focusSynchronized = controller == null ||
        (active != null &&
            controller.active?.document.descriptor.sceneId ==
                active.descriptor.sceneId &&
            controller.active?.document.descriptor.sceneRevision ==
                active.descriptor.sceneRevision);

    final interactionsEnabled = liveActive != null &&
        focusSynchronized &&
        !host.focusTransitionPending &&
        !hasPending &&
        !isMaskActive &&
        (controller?.activeRendererHealthy ?? false);

    AvaSvgCanvasController? liveController() {
      if (controller == null || !identical(host.controller, controller)) {
        return null;
      }
      return controller;
    }

    final previewVisible = draft != null || candidate != null;
    final String titleText;
    if (hasPending && previewVisible) {
      titleText = 'Finishing drawing…';
    } else if (isPendingNew) {
      titleText = '';
    } else {
      titleText = active?.manifest.title ?? '';
    }

    final String counterText;
    if (host.sceneCount > 0 && !isPendingNew) {
      counterText = '${host.currentSceneIndex + 1} / ${host.sceneCount}';
    } else {
      counterText = '';
    }

    final canSave = !hasPending && active != null;

    return Material(
      color: const Color(0xFFF8FBFB),
      elevation: 18,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(28)),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SafeArea(
                top: false,
                bottom: false,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  child: SizedBox(
                    key: const ValueKey('ava_canvas_navbar'),
                    height: 33,
                    child: Row(
                      children: [
                        const SizedBox(width: 44),
                        Expanded(
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Flexible(
                                child: Text(
                                  titleText,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(
                                    color: AvaColors.ink,
                                    fontSize: 15,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                              if (counterText.isNotEmpty) ...[
                                const SizedBox(width: 6),
                                Text(
                                  counterText,
                                  style: TextStyle(
                                    color: AvaColors.ink.withValues(alpha: 0.6),
                                    fontSize: 14,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ],
                            ],
                          ),
                        ),
                        const SizedBox(width: 44 * 3),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 2),
          Expanded(
            child: Stack(
              fit: StackFit.expand,
              children: [
                IgnorePointer(
                  ignoring: !interactionsEnabled || isMaskActive,
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
                if (isMaskActive)
                  const Positioned.fill(
                    child: _TransientCanvasSurface(),
                  ),
              ],
            ),
          ),
        ],
      ),
      Positioned(
        top: 0,
        left: 0,
        right: 0,
        height: 44,
        child: SafeArea(
          top: false,
          bottom: false,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 6),
            child: Row(
              children: [
                Semantics(
                  button: true,
                  label: 'Previous drawing',
                  child: IconButton(
                    tooltip: 'Previous drawing',
                    padding: const EdgeInsets.only(bottom: 11),
                    constraints: const BoxConstraints(
                      minWidth: 44,
                      minHeight: 44,
                    ),
                    onPressed: host.canSelectPrevious
                        ? () => unawaited(host.selectPreviousScene())
                        : null,
                    icon: const Icon(CupertinoIcons.chevron_left, size: 18),
                    color: AvaColors.ink.withValues(
                      alpha: host.canSelectPrevious ? 0.72 : 0.28,
                    ),
                  ),
                ),
                const Spacer(),
                Semantics(
                  button: true,
                  label: 'Next drawing',
                  child: IconButton(
                    tooltip: 'Next drawing',
                    padding: const EdgeInsets.only(bottom: 11),
                    constraints: const BoxConstraints(
                      minWidth: 44,
                      minHeight: 44,
                    ),
                    onPressed: host.canSelectNext
                        ? () => unawaited(host.selectNextScene())
                        : null,
                    icon: const Icon(CupertinoIcons.chevron_right, size: 18),
                    color: AvaColors.ink.withValues(
                      alpha: host.canSelectNext ? 0.72 : 0.28,
                    ),
                  ),
                ),
                Semantics(
                  button: true,
                  label: 'Save drawing',
                  child: IconButton(
                    tooltip: 'Save drawing',
                    padding: const EdgeInsets.only(bottom: 11),
                    constraints: const BoxConstraints(
                      minWidth: 44,
                      minHeight: 44,
                    ),
                    onPressed: canSave
                        ? () {
                            developer.log('canvas_save_requested',
                                name: 'ava.canvas.save');
                            unawaited(saveService.saveSvg(document: active));
                          }
                        : null,
                    icon: const Icon(
                      CupertinoIcons.arrow_down_doc,
                      size: 18,
                    ),
                    color: AvaColors.ink.withValues(
                      alpha: canSave ? 0.72 : 0.28,
                    ),
                  ),
                ),
                Semantics(
                  button: true,
                  label: 'Close drawing',
                  child: IconButton(
                    tooltip: 'Close drawing',
                    padding: const EdgeInsets.only(bottom: 11),
                    constraints: const BoxConstraints(
                      minWidth: 44,
                      minHeight: 44,
                    ),
                    onPressed: host.dismissPanel,
                    icon: const Icon(
                      CupertinoIcons.xmark_circle_fill,
                      size: 18,
                    ),
                    color: AvaColors.ink.withValues(alpha: 0.72),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    ],
  ),
);
  }
}

final class _TransientCanvasSurface extends StatelessWidget {
  const _TransientCanvasSurface();

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      label: 'Ava is drawing a visual',
      child: Container(
        color: const Color(0xFFF8FBFB),
        alignment: Alignment.center,
        child: const Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox.square(
              dimension: 22,
              child: CircularProgressIndicator(
                strokeWidth: 2.5,
                color: AvaColors.teal,
              ),
            ),
            SizedBox(height: 12),
            Text(
              'Drawing…',
              style: TextStyle(
                color: AvaColors.ink,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
