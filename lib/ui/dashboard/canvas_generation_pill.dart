import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

/// Floating circular indicator shown on the bottom right while Ava is
/// drawing a visual in the background.
class CanvasGenerationPill extends StatefulWidget {
  const CanvasGenerationPill({
    super.key,
    required this.visible,
  });

  final bool visible;

  @override
  State<CanvasGenerationPill> createState() => _CanvasGenerationPillState();
}

class _CanvasGenerationPillState extends State<CanvasGenerationPill>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _pulse;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
    _pulse = CurvedAnimation(
      parent: _controller,
      curve: Curves.easeInOutSine,
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedScale(
      scale: widget.visible ? 1.0 : 0.0,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOutBack,
      child: AnimatedOpacity(
        opacity: widget.visible ? 1.0 : 0.0,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
        child: IgnorePointer(
          ignoring: !widget.visible,
          child: Semantics(
            label: 'Ava is drawing a visual',
            child: Tooltip(
              message: 'Ava is drawing a visual...',
              child: AnimatedBuilder(
                animation: _pulse,
                builder: (context, child) {
                  return Container(
                    width: 46,
                    height: 46,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: const Color(0xFF2C353F).withValues(alpha: 0.92),
                      border: Border.all(
                        color: const Color(0xFFFFB38A).withValues(
                          alpha: 0.4 + (_pulse.value * 0.4),
                        ),
                        width: 1.5,
                      ),
                      boxShadow: [
                        BoxShadow(
                          color: const Color(0xFFFFB38A).withValues(
                            alpha: 0.25 + (_pulse.value * 0.25),
                          ),
                          blurRadius: 14 + (_pulse.value * 8),
                          spreadRadius: 1 + (_pulse.value * 2),
                        ),
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.3),
                          blurRadius: 8,
                          offset: const Offset(0, 3),
                        ),
                      ],
                    ),
                    child: Transform.rotate(
                      angle: (_pulse.value - 0.5) * 0.4,
                      child: const Icon(
                        CupertinoIcons.paintbrush_fill,
                        size: 20,
                        color: Color(0xFFFFB38A),
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
        ),
      ),
    );
  }
}
