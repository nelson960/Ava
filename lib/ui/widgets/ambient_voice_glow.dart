import 'package:flutter/material.dart';

/// Renders a soft, organic ambient glow around the screen edges when
/// microphone or conversation speech is active.
class AmbientVoiceGlow extends StatefulWidget {
  const AmbientVoiceGlow({
    super.key,
    required this.active,
    this.accentColor = const Color(0xFFFFB38A),
    this.child,
  });

  final bool active;
  final Color accentColor;
  final Widget? child;

  @override
  State<AmbientVoiceGlow> createState() => _AmbientVoiceGlowState();
}

class _AmbientVoiceGlowState extends State<AmbientVoiceGlow>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _pulse;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2200),
    );
    _pulse = CurvedAnimation(
      parent: _controller,
      curve: Curves.easeInOutSine,
    );
    if (widget.active) {
      _controller.repeat(reverse: true);
    }
  }

  @override
  void didUpdateWidget(AmbientVoiceGlow oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active != oldWidget.active) {
      if (widget.active) {
        _controller.repeat(reverse: true);
      } else {
        _controller.animateTo(0.0, duration: const Duration(milliseconds: 400));
      }
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        if (widget.child != null) widget.child!,
        Positioned.fill(
          child: IgnorePointer(
            child: AnimatedBuilder(
              animation: _pulse,
              builder: (context, _) {
                final opacity = widget.active
                    ? 0.35 + (_pulse.value * 0.45)
                    : 0.0;
                if (opacity <= 0.001) {
                  return const SizedBox.shrink();
                }
                return CustomPaint(
                  painter: _AmbientGlowPainter(
                    color: widget.accentColor.withValues(alpha: opacity),
                    pulseRatio: _pulse.value,
                  ),
                );
              },
            ),
          ),
        ),
      ],
    );
  }
}

class _AmbientGlowPainter extends CustomPainter {
  const _AmbientGlowPainter({
    required this.color,
    required this.pulseRatio,
  });

  final Color color;
  final double pulseRatio;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final blur = 24.0 + (pulseRatio * 16.0);
    final paint = Paint()
      ..shader = RadialGradient(
        center: Alignment.bottomCenter,
        radius: 1.2 + (pulseRatio * 0.15),
        colors: [
          color.withValues(alpha: 0.18),
          color.withValues(alpha: 0.08),
          Colors.transparent,
        ],
        stops: const [0.0, 0.55, 1.0],
      ).createShader(rect);

    canvas.drawRect(rect, paint);

    // Rim aura around perimeter
    final rimPaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5 + (pulseRatio * 2.5)
      ..maskFilter = MaskFilter.blur(BlurStyle.normal, blur)
      ..color = color.withValues(alpha: 0.45);

    final rrect = RRect.fromRectAndRadius(
      rect.deflate(2.0),
      const Radius.circular(24),
    );
    canvas.drawRRect(rrect, rimPaint);
  }

  @override
  bool shouldRepaint(_AmbientGlowPainter oldDelegate) =>
      color != oldDelegate.color || pulseRatio != oldDelegate.pulseRatio;
}
