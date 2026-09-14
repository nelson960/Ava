import 'dart:async';
import 'dart:developer' as developer;

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../../canvas/ava_svg_canvas_controller.dart';
import '../../canvas/ava_svg_canvas_session_host.dart';
import '../../services/ava_canvas_save_service.dart';
import '../design/ava_design.dart';
import 'ava_svg_canvas_view.dart';

final class AvaSvgCanvasPanel extends StatelessWidget {
  const AvaSvgCanvasPanel({
    super.key,
    required this.host,
    this.saveService = const IoAvaCanvasSaveService(),
  });

  final AvaSvgCanvasSessionHost host;
  final AvaCanvasSaveService saveService;

  @override
  Widget build(BuildContext context) {
    final controller = host.controller;
    final liveActive = controller?.active?.document;
    final hasPending = controller?.hasPendingVisual ?? false;
    final mayFocus = controller?.generationMayFocus ?? true;
    final isPendingNew =
        hasPending && mayFocus && controller?.generation?.operation == 'new';
    final active = isPendingNew ? null : (liveActive ?? host.presentedDocument);
    final draft = mayFocus ? controller?.draft?.document : null;
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
                        foregroundCandidate: mayFocus,
                        onCandidateRetained: () =>
                            liveController()?.retainPreparedCandidate(),
                        presentCandidate:
                            liveController()?.shouldPresentCandidate ?? false,
                        interactionsEnabled: interactionsEnabled,
                        onDraftPresented: (presentedDraft) {
                          final ownedController = liveController();
                          if (ownedController?.draft?.document ==
                              presentedDraft) {
                            ownedController!.draftPresented(presentedDraft);
                          }
                        },
                        onCandidatePrepared: (preparedDocument) {
                          final ownedController = liveController();
                          final state = ownedController?.candidate;
                          if (identical(state?.document, preparedDocument) &&
                              state?.phase ==
                                  AvaSvgCanvasCandidatePhase
                                      .rendererPreparing) {
                            ownedController!.rendererPrepared();
                          }
                        },
                        onCandidateFirstFramePresented: (presentedDocument) {
                          final ownedController = liveController();
                          final state = ownedController?.candidate;
                          if (identical(state?.document, presentedDocument) &&
                              state?.phase ==
                                  AvaSvgCanvasCandidatePhase
                                      .presentationRequested) {
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
                              ownedController
                                  .candidate?.descriptor.generationId ??
                              ownedController
                                  .draft?.document.descriptor.generationId ??
                              ownedController
                                  .active?.document.descriptor.generationId;
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
                    AvaCanvasNavbarButton(
                      tooltip: 'Previous drawing',
                      semanticsLabel: 'Previous drawing',
                      onPressed: host.sceneCount > 0
                          ? host.requestPreviousScene
                          : null,
                      icon: const Icon(CupertinoIcons.chevron_left, size: 18),
                      color: AvaColors.ink.withValues(
                        alpha: host.sceneCount > 0 ? 0.72 : 0.28,
                      ),
                    ),
                    const Spacer(),
                    AvaCanvasNavbarButton(
                      tooltip: 'Next drawing',
                      semanticsLabel: 'Next drawing',
                      onPressed:
                          host.sceneCount > 0 ? host.requestNextScene : null,
                      icon: const Icon(CupertinoIcons.chevron_right, size: 18),
                      color: AvaColors.ink.withValues(
                        alpha: host.sceneCount > 0 ? 0.72 : 0.28,
                      ),
                    ),
                    AvaCanvasNavbarButton(
                      tooltip: 'Save drawing',
                      semanticsLabel: 'Save drawing',
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
                    AvaCanvasNavbarButton(
                      tooltip: 'Close drawing',
                      semanticsLabel: 'Close drawing',
                      onPressed: host.dismissPanel,
                      icon: const Icon(
                        CupertinoIcons.xmark_circle_fill,
                        size: 18,
                      ),
                      color: AvaColors.ink.withValues(alpha: 0.72),
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

final class AvaCanvasNavbarButton extends StatefulWidget {
  const AvaCanvasNavbarButton({
    super.key,
    required this.tooltip,
    required this.semanticsLabel,
    required this.icon,
    required this.onPressed,
    this.color,
    this.constraints = const BoxConstraints(minWidth: 44, minHeight: 44),
    this.visualSize = 32.0,
  });

  final String tooltip;
  final String semanticsLabel;
  final Widget icon;
  final VoidCallback? onPressed;
  final Color? color;
  final BoxConstraints? constraints;
  final double visualSize;

  @override
  State<AvaCanvasNavbarButton> createState() => _AvaCanvasNavbarButtonState();
}

class _AvaCanvasNavbarButtonState extends State<AvaCanvasNavbarButton> {
  bool _isHovered = false;
  bool _isPressed = false;
  bool _isFocused = false;

  @override
  Widget build(BuildContext context) {
    final enabled = widget.onPressed != null;
    final baseColor = widget.color ?? AvaColors.ink;
    final effectiveColor =
        enabled ? baseColor : baseColor.withValues(alpha: 0.28);

    Color overlayColor;
    if (!enabled) {
      overlayColor = Colors.transparent;
    } else if (_isPressed) {
      overlayColor = AvaColors.ink.withValues(alpha: 0.12);
    } else if (_isHovered) {
      overlayColor = AvaColors.ink.withValues(alpha: 0.07);
    } else if (_isFocused) {
      overlayColor = AvaColors.ink.withValues(alpha: 0.08);
    } else {
      overlayColor = Colors.transparent;
    }

    final topOffset = (33.0 - widget.visualSize) / 2.0;
    final leftOffset = (44.0 - widget.visualSize) / 2.0;

    return Semantics(
      button: true,
      enabled: enabled,
      label: widget.semanticsLabel,
      child: Tooltip(
        message: widget.tooltip,
        child: FocusableActionDetector(
          enabled: enabled,
          onShowFocusHighlight: (v) => setState(() => _isFocused = v),
          actions: {
            ActivateIntent: CallbackAction<ActivateIntent>(
              onInvoke: (_) => widget.onPressed?.call(),
            ),
          },
          mouseCursor:
              enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
          child: MouseRegion(
            cursor:
                enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
            onEnter: enabled ? (_) => setState(() => _isHovered = true) : null,
            onExit: enabled ? (_) => setState(() => _isHovered = false) : null,
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTapDown:
                  enabled ? (_) => setState(() => _isPressed = true) : null,
              onTapUp:
                  enabled ? (_) => setState(() => _isPressed = false) : null,
              onTapCancel:
                  enabled ? () => setState(() => _isPressed = false) : null,
              onTap: widget.onPressed,
              child: ConstrainedBox(
                constraints: widget.constraints ??
                    const BoxConstraints(minWidth: 44, minHeight: 44),
                child: SizedBox(
                  width: 44,
                  height: 44,
                  child: Stack(
                    alignment: Alignment.topCenter,
                    children: [
                      Positioned(
                        top: topOffset,
                        left: leftOffset,
                        width: widget.visualSize,
                        height: widget.visualSize,
                        child: AnimatedContainer(
                          key: ValueKey(
                              'navbar_button_visual_${widget.tooltip}'),
                          duration: const Duration(milliseconds: 100),
                          decoration: ShapeDecoration(
                            shape: const CircleBorder(),
                            color: overlayColor,
                          ),
                          alignment: Alignment.center,
                          child: IconTheme(
                            data: IconThemeData(
                              color: effectiveColor,
                              size: 18,
                            ),
                            child: widget.icon,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
