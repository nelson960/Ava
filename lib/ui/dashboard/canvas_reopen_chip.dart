import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

/// Floating circular button shown on the bottom right when an interactive
/// visual exists but has been dismissed, allowing the child to reopen it.
class CanvasReopenChip extends StatefulWidget {
  const CanvasReopenChip({
    super.key,
    required this.visible,
    required this.onPressed,
    this.label = 'View Drawing',
  });

  final bool visible;
  final VoidCallback onPressed;
  final String label;

  @override
  State<CanvasReopenChip> createState() => _CanvasReopenChipState();
}

class _CanvasReopenChipState extends State<CanvasReopenChip>
    with TickerProviderStateMixin {
  late final AnimationController _touchController;
  late final Animation<double> _scaleAnimation;
  late final AnimationController _paintController;

  @override
  void initState() {
    super.initState();
    _touchController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 120),
      lowerBound: 0.0,
      upperBound: 1.0,
    );
    _scaleAnimation = Tween<double>(begin: 1.0, end: 0.88).animate(
      CurvedAnimation(parent: _touchController, curve: Curves.easeOutCubic),
    );
    _paintController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1800),
    )..repeat();
  }

  @override
  void dispose() {
    _touchController.dispose();
    _paintController.dispose();
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
            button: true,
            label: widget.label,
            child: Tooltip(
              message: widget.label,
              child: AnimatedBuilder(
                animation: _scaleAnimation,
                builder: (context, child) {
                  return Transform.scale(
                    scale: _scaleAnimation.value,
                    child: child,
                  );
                },
                child: Material(
                  color: const Color(0xFF2C353F).withValues(alpha: 0.92),
                  shape: const CircleBorder(
                    side: BorderSide(
                      color: Color(0xFFFFB38A),
                      width: 1.5,
                    ),
                  ),
                  elevation: 5,
                  shadowColor: const Color(0xFFFFB38A).withValues(alpha: 0.35),
                  child: InkWell(
                    customBorder: const CircleBorder(),
                    onTapDown: (_) => _touchController.forward(),
                    onTapUp: (_) => _touchController.reverse(),
                    onTapCancel: () => _touchController.reverse(),
                    onTap: widget.onPressed,
                    child: SizedBox(
                      width: 46,
                      height: 46,
                      child: RotationTransition(
                        turns: Tween<double>(begin: 0, end: 1).animate(
                          CurvedAnimation(
                            parent: _paintController,
                            curve: Curves.easeInOutSine,
                          ),
                        ),
                        child: const Icon(
                          CupertinoIcons.paintbrush_fill,
                          size: 20,
                          color: Color(0xFFFFB38A),
                        ),
                      ),
                    ),
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
