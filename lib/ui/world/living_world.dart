import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

enum LivingWorldDayPhase {
  dawn,
  day,
  dusk,
  night,
}

enum LivingWorldElement {
  sun,
  moon,
  cloudLeft,
  cloudRight,
  starLeft,
  starCenter,
  starRight,
}

typedef LivingWorldClock = DateTime Function();

class LivingWorldState {
  const LivingWorldState({
    required this.phase,
    required this.phaseProgress,
    required this.sunProgress,
    required this.moonProgress,
    required this.sunOpacity,
    required this.moonOpacity,
    required this.starOpacity,
  });

  factory LivingWorldState.fromLocalTime(DateTime localTime) {
    final minutes = localTime.hour * 60 +
        localTime.minute +
        localTime.second / Duration.secondsPerMinute;

    final LivingWorldDayPhase phase;
    final double phaseProgress;
    if (minutes >= 300 && minutes < 480) {
      phase = LivingWorldDayPhase.dawn;
      phaseProgress = (minutes - 300) / 180;
    } else if (minutes >= 480 && minutes < 1020) {
      phase = LivingWorldDayPhase.day;
      phaseProgress = (minutes - 480) / 540;
    } else if (minutes >= 1020 && minutes < 1200) {
      phase = LivingWorldDayPhase.dusk;
      phaseProgress = (minutes - 1020) / 180;
    } else {
      phase = LivingWorldDayPhase.night;
      final nightMinutes = minutes >= 1200 ? minutes - 1200 : minutes + 240;
      phaseProgress = nightMinutes / 540;
    }

    final sunProgress = ((minutes - 300) / 900).clamp(0.0, 1.0);
    final sunOpacity = minutes >= 300 && minutes < 1200
        ? math.sin(math.pi * sunProgress).clamp(0.0, 1.0)
        : 0.0;
    final moonMinutes = minutes >= 1200 ? minutes - 1200 : minutes + 240;
    final moonProgress = (moonMinutes / 540).clamp(0.0, 1.0);
    final moonOpacity =
        phase == LivingWorldDayPhase.night ? _edgeOpacity(phaseProgress) : 0.0;
    final starOpacity = switch (phase) {
      LivingWorldDayPhase.night => _edgeOpacity(phaseProgress),
      LivingWorldDayPhase.dawn => (1 - phaseProgress * 2).clamp(0.0, 1.0),
      LivingWorldDayPhase.dusk =>
        ((phaseProgress - 0.42) / 0.58).clamp(0.0, 1.0),
      LivingWorldDayPhase.day => 0.0,
    };

    return LivingWorldState(
      phase: phase,
      phaseProgress: phaseProgress.clamp(0.0, 1.0),
      sunProgress: sunProgress,
      moonProgress: moonProgress,
      sunOpacity: sunOpacity,
      moonOpacity: moonOpacity,
      starOpacity: starOpacity,
    );
  }

  final LivingWorldDayPhase phase;
  final double phaseProgress;
  final double sunProgress;
  final double moonProgress;
  final double sunOpacity;
  final double moonOpacity;
  final double starOpacity;

  static double _edgeOpacity(double progress) {
    return math.sin(math.pi * progress).clamp(0.0, 1.0);
  }
}

class LivingWorldController extends ChangeNotifier {
  LivingWorldController({
    required TickerProvider vsync,
    LivingWorldClock? clock,
  })  : _clock = clock ?? DateTime.now,
        _ambient = AnimationController(
          vsync: vsync,
          duration: const Duration(seconds: 96),
        ),
        _reaction = AnimationController(
          vsync: vsync,
          duration: const Duration(milliseconds: 900),
        ) {
    _state = LivingWorldState.fromLocalTime(_clock());
    _ambient.repeat();
    _clockTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => refreshTime(),
    );
    repaint = Listenable.merge([this, _ambient, _reaction]);
  }

  static const interactionCooldown = Duration(milliseconds: 700);

  final LivingWorldClock _clock;
  final AnimationController _ambient;
  final AnimationController _reaction;
  final Map<LivingWorldElement, DateTime> _lastInteraction = {};
  late final Listenable repaint;
  late LivingWorldState _state;
  Timer? _clockTimer;
  LivingWorldElement? _reactingElement;

  LivingWorldState get state => _state;
  double get ambientProgress => _ambient.value;
  double get reactionProgress => _reaction.value;
  LivingWorldElement? get reactingElement => _reactingElement;

  void refreshTime() {
    final next = LivingWorldState.fromLocalTime(_clock());
    if (_equivalentTimeState(_state, next)) return;
    _state = next;
    notifyListeners();
  }

  bool interact(LivingWorldElement element) {
    if (!_isVisible(element)) return false;
    final now = _clock();
    final previous = _lastInteraction[element];
    if (previous != null && now.difference(previous) < interactionCooldown) {
      return false;
    }
    _lastInteraction[element] = now;
    _reactingElement = element;
    _reaction.forward(from: 0);
    notifyListeners();
    return true;
  }

  bool _isVisible(LivingWorldElement element) {
    return switch (element) {
      LivingWorldElement.sun => _state.sunOpacity > 0.08,
      LivingWorldElement.moon => _state.moonOpacity > 0.08,
      LivingWorldElement.cloudLeft || LivingWorldElement.cloudRight => true,
      LivingWorldElement.starLeft ||
      LivingWorldElement.starCenter ||
      LivingWorldElement.starRight =>
        _state.starOpacity > 0.08,
    };
  }

  bool _equivalentTimeState(
    LivingWorldState previous,
    LivingWorldState next,
  ) {
    const threshold = 0.0001;
    return previous.phase == next.phase &&
        (previous.phaseProgress - next.phaseProgress).abs() < threshold &&
        (previous.sunProgress - next.sunProgress).abs() < threshold &&
        (previous.moonProgress - next.moonProgress).abs() < threshold;
  }

  @override
  void dispose() {
    _clockTimer?.cancel();
    _clockTimer = null;
    _ambient.dispose();
    _reaction.dispose();
    super.dispose();
  }
}

class LivingWorldBackdrop extends StatelessWidget {
  const LivingWorldBackdrop({
    super.key,
    required this.controller,
  });

  final LivingWorldController controller;

  @override
  Widget build(BuildContext context) {
    return RepaintBoundary(
      child: CustomPaint(
        painter: _LivingWorldPainter(controller),
        size: Size.infinite,
      ),
    );
  }
}

class LivingWorldInteractionLayer extends StatelessWidget {
  const LivingWorldInteractionLayer({
    super.key,
    required this.controller,
  });

  final LivingWorldController controller;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return LayoutBuilder(
          builder: (context, constraints) {
            final geometry = _LivingWorldGeometry(
              Size(constraints.maxWidth, constraints.maxHeight),
              controller.state,
            );
            return Stack(
              children: [
                if (controller.state.sunOpacity > 0.08)
                  _WorldHitTarget(
                    key: const ValueKey('living-world-sun'),
                    center: geometry.sunCenter,
                    size: 74,
                    semanticsLabel: 'Touch the sun',
                    onTap: () => controller.interact(LivingWorldElement.sun),
                  ),
                if (controller.state.moonOpacity > 0.08)
                  _WorldHitTarget(
                    key: const ValueKey('living-world-moon'),
                    center: geometry.moonCenter,
                    size: 78,
                    semanticsLabel: 'Touch the moon',
                    onTap: () => controller.interact(LivingWorldElement.moon),
                  ),
                _WorldHitTarget(
                  key: const ValueKey('living-world-cloud-left'),
                  center: geometry.cloudLeftCenter,
                  width: 112,
                  height: 70,
                  semanticsLabel: 'Touch a cloud',
                  onTap: () =>
                      controller.interact(LivingWorldElement.cloudLeft),
                ),
                _WorldHitTarget(
                  key: const ValueKey('living-world-cloud-right'),
                  center: geometry.cloudRightCenter,
                  width: 112,
                  height: 70,
                  semanticsLabel: 'Touch a cloud',
                  onTap: () =>
                      controller.interact(LivingWorldElement.cloudRight),
                ),
                if (controller.state.starOpacity > 0.08) ...[
                  _WorldHitTarget(
                    key: const ValueKey('living-world-star-left'),
                    center: geometry.starCenters[1],
                    size: 42,
                    semanticsLabel: 'Touch a star',
                    onTap: () =>
                        controller.interact(LivingWorldElement.starLeft),
                  ),
                  _WorldHitTarget(
                    key: const ValueKey('living-world-star-center'),
                    center: geometry.starCenters[4],
                    size: 42,
                    semanticsLabel: 'Touch a star',
                    onTap: () =>
                        controller.interact(LivingWorldElement.starCenter),
                  ),
                  _WorldHitTarget(
                    key: const ValueKey('living-world-star-right'),
                    center: geometry.starCenters[7],
                    size: 42,
                    semanticsLabel: 'Touch a star',
                    onTap: () =>
                        controller.interact(LivingWorldElement.starRight),
                  ),
                ],
              ],
            );
          },
        );
      },
    );
  }
}

class _WorldHitTarget extends StatelessWidget {
  const _WorldHitTarget({
    super.key,
    required this.center,
    required this.semanticsLabel,
    required this.onTap,
    this.size,
    this.width,
    this.height,
  });

  final Offset center;
  final String semanticsLabel;
  final VoidCallback onTap;
  final double? size;
  final double? width;
  final double? height;

  @override
  Widget build(BuildContext context) {
    final targetWidth = width ?? size;
    final targetHeight = height ?? size;
    if (targetWidth == null || targetHeight == null) {
      throw StateError('A living-world target requires finite dimensions.');
    }
    return Positioned(
      left: center.dx - targetWidth / 2,
      top: center.dy - targetHeight / 2,
      width: targetWidth,
      height: targetHeight,
      child: Semantics(
        button: true,
        label: semanticsLabel,
        child: GestureDetector(
          behavior: HitTestBehavior.translucent,
          onTap: onTap,
        ),
      ),
    );
  }
}

class _LivingWorldPainter extends CustomPainter {
  _LivingWorldPainter(this.controller) : super(repaint: controller.repaint);

  final LivingWorldController controller;

  @override
  void paint(Canvas canvas, Size size) {
    final state = controller.state;
    final palette = _LivingWorldPalette.forState(state);
    final geometry = _LivingWorldGeometry(size, state);
    final bounds = Offset.zero & size;
    final ambient = controller.ambientProgress * math.pi * 2;
    final reaction = Curves.easeOutCubic.transform(
      controller.reactionProgress,
    );

    canvas.drawRect(
      bounds,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [palette.top, palette.middle, palette.bottom],
          stops: const [0, 0.54, 1],
        ).createShader(bounds),
    );
    canvas.drawRect(
      bounds,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(0, -0.04),
          radius: 0.78,
          colors: [
            palette.contrast.withValues(alpha: palette.contrastOpacity),
            palette.contrast.withValues(alpha: 0),
          ],
        ).createShader(bounds),
    );

    _paintStars(canvas, geometry, palette, ambient, reaction);
    _paintCelestialBodies(canvas, geometry, palette, reaction);
    _paintClouds(canvas, geometry, palette, ambient, reaction);
  }

  void _paintCelestialBodies(
    Canvas canvas,
    _LivingWorldGeometry geometry,
    _LivingWorldPalette palette,
    double reaction,
  ) {
    final reacting = controller.reactingElement;
    if (controller.state.sunOpacity > 0.001) {
      final pulse = reacting == LivingWorldElement.sun
          ? math.sin(math.pi * reaction).clamp(0.0, 1.0)
          : 0.0;
      _paintGlow(
        canvas,
        geometry.sunCenter,
        58 + pulse * 18,
        palette.sun,
        (0.16 + pulse * 0.16) * controller.state.sunOpacity,
      );
      canvas.drawCircle(
        geometry.sunCenter,
        22 + pulse * 2.5,
        Paint()
          ..color = palette.sun.withValues(
            alpha: controller.state.sunOpacity,
          ),
      );
    }
    if (controller.state.moonOpacity > 0.001) {
      final pulse = reacting == LivingWorldElement.moon
          ? math.sin(math.pi * reaction).clamp(0.0, 1.0)
          : 0.0;
      _paintGlow(
        canvas,
        geometry.moonCenter,
        54 + pulse * 22,
        palette.moon,
        (0.12 + pulse * 0.18) * controller.state.moonOpacity,
      );
      final outer = Path()
        ..addOval(
          Rect.fromCircle(center: geometry.moonCenter, radius: 23),
        );
      final inner = Path()
        ..addOval(
          Rect.fromCircle(
            center: geometry.moonCenter.translate(9, -4),
            radius: 21,
          ),
        );
      final crescent = Path.combine(PathOperation.difference, outer, inner);
      canvas.drawPath(
        crescent,
        Paint()
          ..color = palette.moon.withValues(
            alpha: controller.state.moonOpacity,
          ),
      );
    }
  }

  void _paintClouds(
    Canvas canvas,
    _LivingWorldGeometry geometry,
    _LivingWorldPalette palette,
    double ambient,
    double reaction,
  ) {
    final drift = math.sin(ambient) * 12;
    _paintCloud(
      canvas,
      geometry.cloudLeftCenter.translate(drift, 0),
      palette.cloud,
      controller.reactingElement == LivingWorldElement.cloudLeft ? reaction : 0,
    );
    _paintCloud(
      canvas,
      geometry.cloudRightCenter.translate(-drift * 0.72, 0),
      palette.cloud,
      controller.reactingElement == LivingWorldElement.cloudRight
          ? reaction
          : 0,
      mirrored: true,
    );
  }

  void _paintCloud(
    Canvas canvas,
    Offset center,
    Color color,
    double reaction, {
    bool mirrored = false,
  }) {
    final impulse = math.sin(math.pi * reaction);
    final shifted = center.translate(
      (mirrored ? -1 : 1) * impulse * 12,
      -impulse * 2,
    );
    final paint = Paint()
      ..color = color.withValues(alpha: 0.24 - impulse * 0.07);
    canvas.drawOval(
      Rect.fromCenter(center: shifted, width: 108, height: 31),
      paint,
    );
    canvas.drawCircle(shifted.translate(-25, -8), 22, paint);
    canvas.drawCircle(shifted.translate(4, -14), 28, paint);
    canvas.drawCircle(shifted.translate(31, -6), 19, paint);
  }

  void _paintStars(
    Canvas canvas,
    _LivingWorldGeometry geometry,
    _LivingWorldPalette palette,
    double ambient,
    double reaction,
  ) {
    if (controller.state.starOpacity <= 0.001) return;
    for (var index = 0; index < geometry.starCenters.length; index += 1) {
      final twinkle =
          0.72 + math.sin(ambient * (1.1 + index * 0.07) + index) * 0.18;
      final selected = switch (controller.reactingElement) {
        LivingWorldElement.starLeft => index == 1,
        LivingWorldElement.starCenter => index == 4,
        LivingWorldElement.starRight => index == 7,
        _ => false,
      };
      final response = selected ? math.sin(math.pi * reaction) : 0.0;
      canvas.drawCircle(
        geometry.starCenters[index],
        1.4 + (index % 3) * 0.45 + response * 2.2,
        Paint()
          ..color = palette.star.withValues(
            alpha: controller.state.starOpacity *
                (twinkle + response * 0.25).clamp(0.0, 1.0),
          ),
      );
    }
  }

  void _paintGlow(
    Canvas canvas,
    Offset center,
    double radius,
    Color color,
    double opacity,
  ) {
    final bounds = Rect.fromCircle(center: center, radius: radius);
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..shader = RadialGradient(
          colors: [
            color.withValues(alpha: opacity),
            color.withValues(alpha: 0),
          ],
        ).createShader(bounds),
    );
  }

  @override
  bool shouldRepaint(covariant _LivingWorldPainter oldDelegate) {
    return oldDelegate.controller != controller;
  }
}

class _LivingWorldGeometry {
  _LivingWorldGeometry(this.size, this.state);

  final Size size;
  final LivingWorldState state;

  Offset get sunCenter => _celestialCenter(state.sunProgress);
  Offset get moonCenter => _celestialCenter(state.moonProgress);

  Offset get cloudLeftCenter => Offset(size.width * 0.14, size.height * 0.48);
  Offset get cloudRightCenter => Offset(size.width * 0.84, size.height * 0.41);

  List<Offset> get starCenters => [
        Offset(size.width * 0.08, size.height * 0.10),
        Offset(size.width * 0.18, size.height * 0.18),
        Offset(size.width * 0.29, size.height * 0.09),
        Offset(size.width * 0.72, size.height * 0.17),
        Offset(size.width * 0.80, size.height * 0.08),
        Offset(size.width * 0.90, size.height * 0.20),
        Offset(size.width * 0.96, size.height * 0.12),
        Offset(size.width * 0.72, size.height * 0.27),
        Offset(size.width * 0.24, size.height * 0.25),
      ];

  Offset _celestialCenter(double progress) {
    final arc = math.sin(math.pi * progress);
    final x = size.width * (0.13 + arc * 0.12);
    final y = size.height * (0.24 - arc * 0.15);
    return Offset(x, y);
  }
}

class _LivingWorldPalette {
  const _LivingWorldPalette({
    required this.top,
    required this.middle,
    required this.bottom,
    required this.contrast,
    required this.contrastOpacity,
    required this.sun,
    required this.moon,
    required this.cloud,
    required this.star,
  });

  static const dawn = _LivingWorldPalette(
    top: Color(0xFFABB8C4),
    middle: Color(0xFFD4CCCE),
    bottom: Color(0xFFE7C5B1),
    contrast: Color(0xFFF0EEEE),
    contrastOpacity: 0.30,
    sun: Color(0xFFFFD98B),
    moon: Color(0xFFE9EEF5),
    cloud: Color(0xFFF0DFDA),
    star: Color(0xFFF9F1E5),
  );
  static const day = _LivingWorldPalette(
    top: Color(0xFFB5C2CA),
    middle: Color(0xFFBFC7C7),
    bottom: Color(0xFFD5DADA),
    contrast: Color(0xFFE8EBEB),
    contrastOpacity: 0.32,
    sun: Color(0xFFFFE5A9),
    moon: Color(0xFFE9EEF5),
    cloud: Color(0xFFF2F4F3),
    star: Color(0xFFFFFFFF),
  );
  static const dusk = _LivingWorldPalette(
    top: Color(0xFF384363),
    middle: Color(0xFF78677E),
    bottom: Color(0xFFC27B72),
    contrast: Color(0xFFE4D8D8),
    contrastOpacity: 0.25,
    sun: Color(0xFFFFCB75),
    moon: Color(0xFFE7EBF4),
    cloud: Color(0xFFD5B7B5),
    star: Color(0xFFFFF2D8),
  );
  static const night = _LivingWorldPalette(
    top: Color(0xFF162038),
    middle: Color(0xFF263654),
    bottom: Color(0xFF3D4D68),
    contrast: Color(0xFF71809A),
    contrastOpacity: 0.24,
    sun: Color(0xFFFFD98B),
    moon: Color(0xFFE8EDF5),
    cloud: Color(0xFF9BA8BC),
    star: Color(0xFFF6F2DD),
  );

  final Color top;
  final Color middle;
  final Color bottom;
  final Color contrast;
  final double contrastOpacity;
  final Color sun;
  final Color moon;
  final Color cloud;
  final Color star;

  factory _LivingWorldPalette.forState(LivingWorldState state) {
    return switch (state.phase) {
      LivingWorldDayPhase.dawn => state.phaseProgress < 0.5
          ? _lerp(night, dawn, state.phaseProgress * 2)
          : _lerp(dawn, day, (state.phaseProgress - 0.5) * 2),
      LivingWorldDayPhase.day => day,
      LivingWorldDayPhase.dusk => state.phaseProgress < 0.5
          ? _lerp(day, dusk, state.phaseProgress * 2)
          : _lerp(dusk, night, (state.phaseProgress - 0.5) * 2),
      LivingWorldDayPhase.night => night,
    };
  }

  static _LivingWorldPalette _lerp(
    _LivingWorldPalette from,
    _LivingWorldPalette to,
    double amount,
  ) {
    return _LivingWorldPalette(
      top: Color.lerp(from.top, to.top, amount)!,
      middle: Color.lerp(from.middle, to.middle, amount)!,
      bottom: Color.lerp(from.bottom, to.bottom, amount)!,
      contrast: Color.lerp(from.contrast, to.contrast, amount)!,
      contrastOpacity: _mix(from.contrastOpacity, to.contrastOpacity, amount),
      sun: Color.lerp(from.sun, to.sun, amount)!,
      moon: Color.lerp(from.moon, to.moon, amount)!,
      cloud: Color.lerp(from.cloud, to.cloud, amount)!,
      star: Color.lerp(from.star, to.star, amount)!,
    );
  }

  static double _mix(double from, double to, double amount) {
    return from + (to - from) * amount;
  }
}
