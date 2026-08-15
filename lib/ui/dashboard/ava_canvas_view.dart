import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/semantics.dart';

import '../../canvas/ava_canvas_engine.dart';
import '../../canvas/ava_canvas_geometry.dart';
import '../../models/ava_canvas_protocol.dart';
import '../design/ava_design.dart';

class AvaCanvasView extends StatefulWidget {
  const AvaCanvasView({
    super.key,
    required this.engine,
    this.onOutcome,
  });

  final AvaCanvasEngine engine;
  final ValueChanged<AvaCanvasInteractionOutcome>? onOutcome;

  @override
  State<AvaCanvasView> createState() => _AvaCanvasViewState();
}

class _AvaCanvasViewState extends State<AvaCanvasView>
    with SingleTickerProviderStateMixin {
  late final Ticker _ticker;
  final Stopwatch _clock = Stopwatch();
  final Map<String, _CanvasAnimationTrack> _tracks = {};
  final Map<String, Map<String, double>> _settledValues = {};
  int _animationGeneration = -1;
  String? _sceneIdentity;
  Duration? _affordanceStart;
  bool _reduceMotion = false;
  int? _activePointer;
  Offset? _pointerOrigin;
  AvaCanvasInteractionBinding? _tapBinding;
  AvaCanvasInteractionBinding? _dragBinding;
  String? _pressedNodeId;
  bool _dragStarted = false;

  @override
  void initState() {
    super.initState();
    _ticker = createTicker((_) => _onFrame());
    widget.engine.addListener(_onEngineChanged);
    _receiveAnimations();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reduceMotion = MediaQuery.disableAnimationsOf(context);
    if (_reduceMotion == reduceMotion) return;
    _reduceMotion = reduceMotion;
    if (_reduceMotion) {
      _finishAllAnimations();
    }
  }

  @override
  void didUpdateWidget(AvaCanvasView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.engine == widget.engine) return;
    oldWidget.engine.removeListener(_onEngineChanged);
    widget.engine.addListener(_onEngineChanged);
    _tracks.clear();
    _settledValues.clear();
    _animationGeneration = -1;
    _sceneIdentity = null;
    _affordanceStart = null;
    _receiveAnimations();
  }

  @override
  void dispose() {
    widget.engine.removeListener(_onEngineChanged);
    _ticker.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scene = widget.engine.scene;
    if (scene == null) return const SizedBox.shrink();
    final animatedValues = _sampledValues();
    final geometry = AvaCanvasGeometry.build(
      widget.engine,
      animatedValues: animatedValues,
    );
    return ClipRect(
      child: Listener(
        behavior: HitTestBehavior.opaque,
        onPointerDown: (event) => _handlePointerDown(event, geometry),
        onPointerMove: (event) => _handlePointerMove(event, geometry),
        onPointerUp: (event) => _handlePointerUp(event, geometry),
        onPointerCancel: _handlePointerCancel,
        child: CustomPaint(
          key: const ValueKey('ava_dynamic_canvas'),
          painter: _AvaCanvasPainter(
            engine: widget.engine,
            geometry: geometry,
            animationFrame: _clock.elapsedMicroseconds,
            pressedNodeId: _pressedNodeId,
            affordanceProgress: _affordanceProgress(),
            onPerformBinding: _performBinding,
          ),
          isComplex: true,
          willChange: _tracks.isNotEmpty || _affordanceIsActive,
          child: const SizedBox.expand(),
        ),
      ),
    );
  }

  void _onEngineChanged() {
    _receiveAnimations();
    if (mounted) setState(() {});
  }

  void _receiveAnimations() {
    final scene = widget.engine.scene;
    final identity = scene == null ? null : '${scene.id}:${scene.revision}';
    if (_sceneIdentity != identity) {
      _sceneIdentity = identity;
      if (!_reduceMotion && (scene?.bindings.isNotEmpty ?? false)) {
        if (!_clock.isRunning) _clock.start();
        _affordanceStart = _clock.elapsed;
        if (!_ticker.isActive) _ticker.start();
      }
    }
    if (_animationGeneration == widget.engine.animationGeneration) return;
    _animationGeneration = widget.engine.animationGeneration;
    final now = _clock.elapsed;
    final activeNodeIds = widget.engine.scene?.nodes.keys.toSet() ?? const {};
    _settledValues.removeWhere((nodeId, _) => !activeNodeIds.contains(nodeId));
    _tracks.removeWhere((key, track) {
      final remove = !activeNodeIds.contains(track.nodeId);
      if (remove) _settledValues[track.nodeId]?.remove(track.property);
      return remove;
    });

    for (final specification in widget.engine.lastAnimations) {
      final key = '${specification.nodeId}:${specification.property}';
      final existing = _tracks[key];
      // A running animation retargets from its rendered value. A completed
      // interaction starts from the newly declared value so repeated taps
      // remain visibly responsive instead of becoming a no-op at 1.0.
      final fromValue = existing?.valueAt(now) ?? specification.fromValue;
      if (_reduceMotion) {
        _settledValues.putIfAbsent(
                specification.nodeId, () => {})[specification.property] =
            specification.toValue;
        _tracks.remove(key);
        continue;
      }
      _tracks[key] = _CanvasAnimationTrack(
        nodeId: specification.nodeId,
        property: specification.property,
        fromValue: fromValue,
        toValue: specification.toValue,
        start: now + specification.delay,
        duration: specification.duration,
        easing: specification.easing,
        repeat: specification.repeat,
        autoreverse: specification.autoreverse,
      );
    }
    if (_tracks.isNotEmpty) {
      if (!_clock.isRunning) _clock.start();
      if (!_ticker.isActive) _ticker.start();
    }
  }

  void _onFrame() {
    if (!mounted) return;
    final now = _clock.elapsed;
    final completed = <String>[];
    for (final entry in _tracks.entries) {
      if (!entry.value.isCompleteAt(now)) continue;
      final track = entry.value;
      _settledValues.putIfAbsent(track.nodeId, () => {})[track.property] =
          track.toValue;
      completed.add(entry.key);
    }
    for (final key in completed) {
      _tracks.remove(key);
    }
    if (!_affordanceIsActive) _affordanceStart = null;
    setState(() {});
    if (_tracks.isEmpty && !_affordanceIsActive) _ticker.stop();
  }

  static const Duration _affordanceDuration = Duration(milliseconds: 1200);

  bool get _affordanceIsActive {
    final started = _affordanceStart;
    return started != null && _clock.elapsed - started < _affordanceDuration;
  }

  double _affordanceProgress() {
    final started = _affordanceStart;
    if (started == null || _reduceMotion) return 1;
    return ((_clock.elapsed - started).inMicroseconds /
            _affordanceDuration.inMicroseconds)
        .clamp(0, 1)
        .toDouble();
  }

  Map<String, Map<String, double>> _sampledValues() {
    final result = {
      for (final entry in _settledValues.entries)
        entry.key: Map<String, double>.of(entry.value),
    };
    final now = _clock.elapsed;
    for (final track in _tracks.values) {
      result.putIfAbsent(track.nodeId, () => {})[track.property] =
          track.valueAt(now);
    }
    return result;
  }

  void _finishAllAnimations() {
    for (final track in _tracks.values) {
      _settledValues.putIfAbsent(track.nodeId, () => {})[track.property] =
          track.toValue;
    }
    _tracks.clear();
    _affordanceStart = null;
    if (_ticker.isActive) _ticker.stop();
    if (mounted) setState(() {});
  }

  void _handlePointerDown(
    PointerDownEvent event,
    AvaCanvasGeometry geometry,
  ) {
    if (_activePointer != null) return;
    final viewport = AvaCanvasViewportTransform.forSize(context.size!);
    final logical = viewport.toLogical(event.localPosition);
    if (!(Offset.zero & avaCanvasLogicalSize).contains(logical)) return;
    _activePointer = event.pointer;
    _pointerOrigin = event.localPosition;
    _tapBinding = geometry.hitTest(
      logical,
      event: AvaCanvasInteractionEvent.tap,
      minimumTargetExtent: 44 / viewport.scale,
    );
    _dragBinding = geometry.hitTest(
      logical,
      event: AvaCanvasInteractionEvent.drag,
      minimumTargetExtent: 44 / viewport.scale,
    );
    _pressedNodeId = (_tapBinding ?? _dragBinding)?.nodeId;
    _dragStarted = false;
    if (_pressedNodeId != null) setState(() {});
  }

  void _handlePointerMove(
    PointerMoveEvent event,
    AvaCanvasGeometry geometry,
  ) {
    if (_activePointer != event.pointer || _dragBinding == null) return;
    final origin = _pointerOrigin;
    if (origin == null) return;
    if (!_dragStarted &&
        (event.localPosition - origin).distance <= kTouchSlop) {
      return;
    }
    _dragStarted = true;
    _performBinding(
      _dragBinding!,
      logicalPosition: AvaCanvasViewportTransform.forSize(context.size!)
          .toLogical(event.localPosition),
    );
  }

  void _handlePointerUp(
    PointerUpEvent event,
    AvaCanvasGeometry geometry,
  ) {
    if (_activePointer != event.pointer) return;
    final viewport = AvaCanvasViewportTransform.forSize(context.size!);
    final logical = viewport.toLogical(event.localPosition);
    if (_dragStarted && _dragBinding != null) {
      _performBinding(_dragBinding!, logicalPosition: logical);
    } else if (_tapBinding != null) {
      _performBinding(_tapBinding!);
    }
    _clearPointer();
  }

  void _handlePointerCancel(PointerCancelEvent event) {
    if (_activePointer == event.pointer) _clearPointer();
  }

  void _performBinding(
    AvaCanvasInteractionBinding binding, {
    Offset? logicalPosition,
  }) {
    final outcome = widget.engine.performInteraction(
      binding.id,
      logicalPosition: logicalPosition,
    );
    widget.onOutcome?.call(outcome);
  }

  void _clearPointer() {
    _activePointer = null;
    _pointerOrigin = null;
    _tapBinding = null;
    _dragBinding = null;
    final hadPressedNode = _pressedNodeId != null;
    _pressedNodeId = null;
    _dragStarted = false;
    if (hadPressedNode && mounted) setState(() {});
  }
}

final class _CanvasAnimationTrack {
  const _CanvasAnimationTrack({
    required this.nodeId,
    required this.property,
    required this.fromValue,
    required this.toValue,
    required this.start,
    required this.duration,
    required this.easing,
    required this.repeat,
    required this.autoreverse,
  });

  final String nodeId;
  final String property;
  final double fromValue;
  final double toValue;
  final Duration start;
  final Duration duration;
  final String easing;
  final bool repeat;
  final bool autoreverse;

  bool isCompleteAt(Duration now) => !repeat && now - start >= duration;

  double valueAt(Duration now) {
    if (duration.inMicroseconds == 0) return toValue;
    final elapsed = now - start;
    if (elapsed.isNegative) return fromValue;
    final elapsedUnits = elapsed.inMicroseconds / duration.inMicroseconds;
    final raw = repeat
        ? (autoreverse
            ? (() {
                final phase = elapsedUnits % 2;
                return phase <= 1 ? phase : 2 - phase;
              })()
            : elapsedUnits % 1)
        : elapsedUnits.clamp(0, 1);
    final progress = switch (easing) {
      'linear' => raw.toDouble(),
      'easeInOut' => Curves.easeInOut.transform(raw.toDouble()),
      'easeOut' => Curves.easeOutCubic.transform(raw.toDouble()),
      _ => throw StateError('Unvalidated canvas easing "$easing".'),
    };
    return fromValue + (toValue - fromValue) * progress;
  }
}

final class _AvaCanvasPainter extends CustomPainter {
  const _AvaCanvasPainter({
    required this.engine,
    required this.geometry,
    required this.animationFrame,
    required this.pressedNodeId,
    required this.affordanceProgress,
    required this.onPerformBinding,
  });

  final AvaCanvasEngine engine;
  final AvaCanvasGeometry geometry;
  final int animationFrame;
  final String? pressedNodeId;
  final double affordanceProgress;
  final void Function(
    AvaCanvasInteractionBinding binding, {
    Offset? logicalPosition,
  }) onPerformBinding;

  @override
  void paint(Canvas canvas, Size size) {
    final viewport = AvaCanvasViewportTransform.forSize(size);
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    viewport.applyTo(canvas);

    final renderNodes = [...geometry.nodes]
      ..sort((first, second) => first.paintOrder.compareTo(second.paintOrder));
    for (final renderNode in renderNodes) {
      if (renderNode.bindings.isEmpty) continue;
      _paintInteractionAffordance(canvas, renderNode);
    }
    for (final renderNode in renderNodes) {
      if (renderNode.node.kind == AvaCanvasNodeKind.group) continue;
      _paintNode(canvas, renderNode);
    }

    final focusedNodeId = engine.scene?.focusedNodeId;
    final focused = focusedNodeId == null ? null : geometry.byId[focusedNodeId];
    if (focused != null) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          focused.logicalBounds.inflate(9),
          const Radius.circular(12),
        ),
        Paint()
          ..color = AvaColors.coral.withValues(alpha: 0.22)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 4,
      );
    }
    final selectedNodeId = engine.scene?.selectedNodeId;
    final selected =
        selectedNodeId == null ? null : geometry.byId[selectedNodeId];
    if (selected != null) {
      final selectedPath = selected.logicalPath;
      if (selectedPath != null) {
        canvas.drawPath(
          selectedPath,
          Paint()
            ..color = AvaColors.teal.withValues(alpha: 0.28)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 9
            ..strokeJoin = StrokeJoin.round
            ..strokeCap = StrokeCap.round,
        );
      }
    }
    canvas.restore();
  }

  void _paintInteractionAffordance(
    Canvas canvas,
    AvaCanvasRenderNode renderNode,
  ) {
    final bounds = renderNode.logicalBounds.inflate(7);
    final pressed = renderNode.node.id == pressedNodeId;
    final pulse = math.sin(affordanceProgress * math.pi);
    if (pressed || pulse > 0.01) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(bounds, const Radius.circular(12)),
        Paint()
          ..color = AvaColors.teal.withValues(
            alpha: pressed ? 0.16 : 0.10 * pulse,
          )
          ..style = pressed ? PaintingStyle.fill : PaintingStyle.stroke
          ..strokeWidth = 3,
      );
    }
    if (renderNode.bindings.any(
      (binding) => binding.event == AvaCanvasInteractionEvent.drag,
    )) {
      final center = Offset(bounds.right - 8, bounds.top + 8);
      for (final offset in const [Offset(-4, 0), Offset(0, 0), Offset(4, 0)]) {
        canvas.drawCircle(
          center + offset,
          1.6,
          Paint()..color = AvaColors.teal.withValues(alpha: 0.7),
        );
      }
    }
    if (renderNode.bindings.any((binding) => binding.actionKind == 'reset')) {
      final center = Offset(bounds.right - 12, bounds.top + 12);
      final paint = Paint()
        ..color = AvaColors.teal.withValues(alpha: 0.78)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.2
        ..strokeCap = StrokeCap.round;
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: 7),
        -0.35,
        math.pi * 1.55,
        false,
        paint,
      );
      canvas.drawPath(
        Path()
          ..moveTo(center.dx - 8, center.dy - 1)
          ..lineTo(center.dx - 3, center.dy - 4)
          ..lineTo(center.dx - 3, center.dy + 2)
          ..close(),
        Paint()..color = AvaColors.teal.withValues(alpha: 0.78),
      );
    }
  }

  void _paintNode(Canvas canvas, AvaCanvasRenderNode renderNode) {
    final json = renderNode.json;
    final style = (json['style']! as Map).cast<String, Object?>();
    final opacity = (style['opacity']! as num).toDouble();
    if (opacity <= 0) return;
    final fill = _styleColor(style, 'fillColor', 'fill').withValues(
      alpha: opacity,
    );
    final stroke = _styleColor(style, 'strokeColor', 'stroke').withValues(
      alpha: opacity,
    );
    final strokeWidth = (style['strokeWidth']! as num).toDouble();
    final strokeCap = switch (style['strokeCap']! as String) {
      'butt' => StrokeCap.butt,
      'round' => StrokeCap.round,
      'square' => StrokeCap.square,
      _ => throw StateError('Unvalidated canvas stroke cap.'),
    };

    if (renderNode.node.kind == AvaCanvasNodeKind.text) {
      final metrics = AvaCanvasNodeGeometry.textMetrics(json);
      final original = metrics.painter.text! as TextSpan;
      metrics.painter.text = TextSpan(
        text: original.text,
        style: original.style?.copyWith(color: fill),
      );
      metrics.painter.layout(
        maxWidth: (json['_layoutMaxWidth'] as num?)?.toDouble() ?? 900,
      );
      canvas.save();
      canvas.transform(renderNode.worldTransform.storage);
      metrics.painter.paint(canvas, metrics.origin);
      canvas.restore();
      return;
    }

    final path = renderNode.logicalPath!;
    if (fill.a > 0 &&
        renderNode.node.kind != AvaCanvasNodeKind.line &&
        renderNode.node.kind != AvaCanvasNodeKind.arrow &&
        renderNode.node.kind != AvaCanvasNodeKind.polyline) {
      final shadow = style['shadow'];
      if (shadow is Map) {
        final value = shadow.cast<String, Object?>();
        canvas.save();
        canvas.translate(
          (value['offsetX']! as num).toDouble(),
          (value['offsetY']! as num).toDouble(),
        );
        canvas.drawShadow(
          path,
          _hexColor(value['color']! as String).withValues(
            alpha: (value['opacity']! as num).toDouble() * opacity,
          ),
          (value['blurRadius']! as num).toDouble(),
          false,
        );
        canvas.restore();
      }
      final gradient = style['gradient'];
      final radialGradient = style['radialGradient'];
      final fillPaint = Paint()
        ..style = PaintingStyle.fill
        ..isAntiAlias = true;
      if (gradient is Map) {
        final value = gradient.cast<String, Object?>();
        Offset point(Object? raw) {
          final point = (raw! as Map).cast<String, Object?>();
          return Offset(
            (point['x']! as num).toDouble(),
            (point['y']! as num).toDouble(),
          );
        }

        final stops = (value['stops']! as List)
            .map((raw) => (raw as Map).cast<String, Object?>())
            .toList(growable: false);
        fillPaint.shader = ui.Gradient.linear(
          point(value['start']),
          point(value['end']),
          [
            for (final stop in stops)
              _hexColor(stop['color']! as String).withValues(alpha: opacity),
          ],
          [
            for (final stop in stops) (stop['offset']! as num).toDouble(),
          ],
        );
      } else if (radialGradient is Map) {
        final value = radialGradient.cast<String, Object?>();
        final center = (value['center']! as Map).cast<String, Object?>();
        final stops = (value['stops']! as List)
            .map((raw) => (raw as Map).cast<String, Object?>())
            .toList(growable: false);
        fillPaint.shader = ui.Gradient.radial(
          Offset(
            (center['x']! as num).toDouble(),
            (center['y']! as num).toDouble(),
          ),
          (value['radius']! as num).toDouble(),
          [
            for (final stop in stops)
              _hexColor(stop['color']! as String).withValues(alpha: opacity),
          ],
          [
            for (final stop in stops) (stop['offset']! as num).toDouble(),
          ],
        );
      } else {
        fillPaint.color = fill;
      }
      canvas.drawPath(
        path,
        fillPaint,
      );
    }
    if (stroke.a > 0 && strokeWidth > 0) {
      canvas.drawPath(
        path,
        Paint()
          ..color = stroke
          ..style = PaintingStyle.stroke
          ..strokeWidth = strokeWidth
          ..strokeCap = strokeCap
          ..strokeJoin = StrokeJoin.round
          ..isAntiAlias = true,
      );
    }
  }

  @override
  SemanticsBuilderCallback get semanticsBuilder => (size) {
        final viewport = AvaCanvasViewportTransform.forSize(size);
        return [
          for (final renderNode in geometry.nodes)
            if (renderNode.bindings.isNotEmpty)
              CustomPainterSemantics(
                rect: viewport.toLocalRect(
                  renderNode.interactionBounds(
                    minimumExtent: 44 / viewport.scale,
                  ),
                ),
                properties: SemanticsProperties(
                  label: renderNode.node.label,
                  hint: _interactionHint(renderNode.bindings),
                  textDirection: TextDirection.ltr,
                  button: renderNode.bindings.any(
                    (binding) => binding.event == AvaCanvasInteractionEvent.tap,
                  ),
                  onTap: renderNode.bindings.any(
                    (binding) => binding.event == AvaCanvasInteractionEvent.tap,
                  )
                      ? () {
                          final binding = renderNode.bindings.lastWhere(
                            (candidate) =>
                                candidate.event ==
                                AvaCanvasInteractionEvent.tap,
                          );
                          onPerformBinding(binding);
                        }
                      : null,
                ),
              ),
        ];
      };

  @override
  bool shouldRepaint(_AvaCanvasPainter oldDelegate) =>
      oldDelegate.geometry != geometry ||
      oldDelegate.animationFrame != animationFrame ||
      oldDelegate.pressedNodeId != pressedNodeId ||
      oldDelegate.affordanceProgress != affordanceProgress;

  @override
  bool shouldRebuildSemantics(_AvaCanvasPainter oldDelegate) =>
      oldDelegate.geometry != geometry;
}

String _interactionHint(List<AvaCanvasInteractionBinding> bindings) {
  if (bindings.any(
    (binding) => binding.event == AvaCanvasInteractionEvent.drag,
  )) {
    return 'Drag to explore';
  }
  final kind = bindings.first.actionKind;
  return switch (kind) {
    'explore' => 'Tap to explore this part',
    'reveal' => 'Tap to reveal',
    'toggleVisibility' => 'Tap to show or hide',
    'select' => 'Tap to select',
    'reset' => 'Tap to reset',
    _ => 'Tap to interact',
  };
}

Color _canvasColor(String token) => switch (token) {
      'ink' => AvaColors.ink,
      'muted' => AvaColors.inkSoft,
      'accent' => AvaColors.coral,
      'accentSoft' => AvaColors.coralSoft,
      'positive' => AvaColors.teal,
      'warning' => AvaColors.gold,
      'paper' => AvaColors.paper,
      'transparent' => Colors.transparent,
      _ => throw StateError('Unvalidated canvas color token "$token".'),
    };

Color _styleColor(
  Map<String, Object?> style,
  String authoredKey,
  String tokenKey,
) {
  final authored = style[authoredKey];
  return authored is String
      ? _hexColor(authored)
      : _canvasColor(style[tokenKey]! as String);
}

Color _hexColor(String value) => Color(
      0xFF000000 | int.parse(value.substring(1), radix: 16),
    );
