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
    if (controller == null) return const SizedBox.shrink();
    final active = controller.active?.document;
    final candidate = controller.candidate?.document;
    final documents = <AvaValidatedSvgCanvasDocument>[
      if (active != null) active,
      if (candidate != null &&
          candidate.descriptor.generationId != active?.descriptor.generationId)
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
              active: identical(document, active),
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
    required AvaSvgCanvasController controller,
    required AvaValidatedSvgCanvasDocument document,
    required bool active,
    required bool candidate,
  }) {
    final generationId = document.descriptor.generationId;
    return KeyedSubtree(
      key: ValueKey('svg-canvas-$generationId'),
      child: IgnorePointer(
        ignoring: !active,
        child: AvaSvgCanvasView(
          document: document,
          present: candidate && controller.shouldPresentCandidate,
          onPrepared: () {
            final state = controller.candidate;
            if (state?.descriptor.generationId == generationId &&
                state?.phase == AvaSvgCanvasCandidatePhase.rendererPreparing) {
              controller.rendererPrepared();
            }
          },
          onFirstFramePresented: () {
            final state = controller.candidate;
            if (state?.descriptor.generationId == generationId &&
                state?.phase ==
                    AvaSvgCanvasCandidatePhase.presentationRequested) {
              controller.firstFramePresented();
            }
          },
          onBinding: controller.reportBinding,
          onError: (message) {
            unawaited(
              controller.rendererFailed(
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
