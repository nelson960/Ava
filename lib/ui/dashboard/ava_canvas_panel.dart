import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../../canvas/ava_canvas_engine.dart';
import '../design/ava_design.dart';
import 'ava_canvas_view.dart';

class AvaCanvasPanel extends StatelessWidget {
  const AvaCanvasPanel({
    super.key,
    required this.engine,
    required this.onClose,
    this.onOutcome,
  });

  final AvaCanvasEngine engine;
  final VoidCallback onClose;
  final ValueChanged<AvaCanvasInteractionOutcome>? onOutcome;

  @override
  Widget build(BuildContext context) {
    if (engine.scene == null) {
      throw StateError('AvaCanvasPanel requires an active canvas scene.');
    }
    return Material(
      color: Colors.transparent,
      child: DecoratedBox(
        decoration: const BoxDecoration(
          color: AvaColors.paper,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(AvaRadii.sheet),
          ),
          border: Border(
            top: BorderSide(color: AvaColors.white, width: 1.2),
          ),
          boxShadow: [
            BoxShadow(
              color: Color(0x260F1B1D),
              blurRadius: 28,
              offset: Offset(0, -8),
            ),
          ],
        ),
        child: SafeArea(
          top: false,
          child: Stack(
            children: [
              Positioned.fill(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 15, 12, 8),
                  child: RepaintBoundary(
                    child: AvaCanvasView(
                      engine: engine,
                      onOutcome: onOutcome,
                    ),
                  ),
                ),
              ),
              Align(
                alignment: Alignment.topCenter,
                child: Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Container(
                    width: 42,
                    height: 4,
                    decoration: BoxDecoration(
                      color: AvaColors.line,
                      borderRadius: BorderRadius.circular(999),
                    ),
                  ),
                ),
              ),
              Positioned(
                top: 8,
                right: 10,
                child: IconButton(
                  key: const ValueKey('close_dynamic_canvas'),
                  tooltip: 'Close drawing',
                  onPressed: onClose,
                  style: IconButton.styleFrom(
                    foregroundColor: AvaColors.inkSoft,
                    backgroundColor: AvaColors.paperMuted,
                    minimumSize: const Size.square(38),
                    maximumSize: const Size.square(38),
                    padding: EdgeInsets.zero,
                  ),
                  icon: const Icon(CupertinoIcons.xmark, size: 16),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
