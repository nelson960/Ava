import 'dart:math' as math;

import 'package:flutter/painting.dart';
import 'package:vector_math/vector_math_64.dart';

import '../models/ava_canvas_protocol.dart';
import 'ava_canvas_engine.dart';
import 'ava_canvas_path_parser.dart';

const avaCanvasLogicalSize = Size(1000, 600);

final class AvaCanvasViewportTransform {
  const AvaCanvasViewportTransform._({
    required this.scale,
    required this.offset,
  });

  factory AvaCanvasViewportTransform.forSize(Size size) {
    if (size.isEmpty) {
      return const AvaCanvasViewportTransform._(
        scale: 1,
        offset: Offset.zero,
      );
    }
    final scale = math.min(
      size.width / avaCanvasLogicalSize.width,
      size.height / avaCanvasLogicalSize.height,
    );
    return AvaCanvasViewportTransform._(
      scale: scale,
      offset: Offset(
        (size.width - avaCanvasLogicalSize.width * scale) / 2,
        (size.height - avaCanvasLogicalSize.height * scale) / 2,
      ),
    );
  }

  final double scale;
  final Offset offset;

  Offset toLogical(Offset localPosition) => (localPosition - offset) / scale;

  Offset toLocal(Offset logicalPosition) => offset + logicalPosition * scale;

  Rect toLocalRect(Rect logicalRect) => Rect.fromPoints(
        toLocal(logicalRect.topLeft),
        toLocal(logicalRect.bottomRight),
      );

  void applyTo(Canvas canvas) {
    canvas.translate(offset.dx, offset.dy);
    canvas.scale(scale);
  }
}

final class AvaCanvasRenderNode {
  const AvaCanvasRenderNode({
    required this.node,
    required this.json,
    required this.worldTransform,
    required this.logicalPath,
    required this.logicalBounds,
    required this.paintOrder,
    required this.bindings,
  });

  final AvaCanvasNode node;
  final Map<String, Object?> json;
  final Matrix4 worldTransform;
  final Path? logicalPath;
  final Rect logicalBounds;
  final int paintOrder;
  final List<AvaCanvasInteractionBinding> bindings;

  Rect interactionBounds({double minimumExtent = 44.0}) {
    return Rect.fromCenter(
      center: logicalBounds.center,
      width: math.max(minimumExtent, logicalBounds.width),
      height: math.max(minimumExtent, logicalBounds.height),
    );
  }
}

final class AvaCanvasGeometry {
  AvaCanvasGeometry._({
    required this.nodes,
    required this.byId,
  });

  final List<AvaCanvasRenderNode> nodes;
  final Map<String, AvaCanvasRenderNode> byId;

  factory AvaCanvasGeometry.build(
    AvaCanvasEngine engine, {
    Map<String, Map<String, double>> animatedValues = const {},
  }) {
    final scene = engine.scene;
    if (scene == null) {
      return AvaCanvasGeometry._(nodes: const [], byId: const {});
    }

    final children = <String?, List<AvaCanvasNode>>{};
    for (final node in scene.nodes.values) {
      children.putIfAbsent(node.parentId, () => []).add(node);
    }
    final bindingsByNode = <String, List<AvaCanvasInteractionBinding>>{};
    for (final binding in scene.bindings.values) {
      bindingsByNode.putIfAbsent(binding.nodeId, () => []).add(binding);
    }
    final exploreCardByTextId = <String, String>{};
    for (final binding in scene.bindings.values) {
      if (binding.actionKind != 'explore') continue;
      final revealNodeIds =
          (binding.action['revealNodeIds']! as List).cast<String>();
      final cards = revealNodeIds
          .where(
              (nodeId) => scene.nodes[nodeId]?.kind == AvaCanvasNodeKind.rect)
          .toList();
      final texts = revealNodeIds
          .where(
              (nodeId) => scene.nodes[nodeId]?.kind == AvaCanvasNodeKind.text)
          .toList();
      if (cards.length == 1 && texts.length == 1) {
        exploreCardByTextId[texts.single] = cards.single;
      }
    }

    final result = <AvaCanvasRenderNode>[];
    final byId = <String, AvaCanvasRenderNode>{};
    final descendantBounds = <String, Rect>{};

    Rect? visit(
      AvaCanvasNode node,
      Matrix4 parentTransform,
      bool ancestorVisible,
    ) {
      final json = engine.effectiveNodeJson(node);
      final cardId = exploreCardByTextId[node.id];
      if (cardId != null) {
        _centerTextInExploreCard(
          json,
          engine.effectiveNodeJson(scene.nodes[cardId]!),
        );
      }
      final animation = animatedValues[node.id];
      if (animation != null) {
        final transform = (json['transform']! as Map).cast<String, Object?>();
        final style = (json['style']! as Map).cast<String, Object?>();
        for (final entry in animation.entries) {
          if (entry.key == 'opacity') {
            style['opacity'] = entry.value;
          } else {
            transform[entry.key] = entry.value;
          }
        }
      }
      final visible = ancestorVisible && json['visible']! as bool;
      final world = parentTransform.clone()
        ..multiply(_nodeTransform(json['transform']! as Map));
      if (!visible) return null;

      Path? logicalPath;
      Rect? ownBounds;
      if (node.kind != AvaCanvasNodeKind.group) {
        final localPath = AvaCanvasNodeGeometry.localPath(node, json);
        logicalPath = localPath.transform(world.storage);
        ownBounds = logicalPath.getBounds();
        final renderNode = AvaCanvasRenderNode(
          node: node,
          json: json,
          worldTransform: world,
          logicalPath: logicalPath,
          logicalBounds: ownBounds,
          paintOrder: result.length,
          bindings: List.unmodifiable(bindingsByNode[node.id] ?? const []),
        );
        result.add(renderNode);
        byId[node.id] = renderNode;
      }

      Rect? combined = ownBounds;
      for (final child in children[node.id] ?? const <AvaCanvasNode>[]) {
        final childBounds = visit(child, world, visible);
        if (childBounds != null) {
          combined = combined == null
              ? childBounds
              : combined.expandToInclude(childBounds);
        }
      }
      if (combined != null) descendantBounds[node.id] = combined;
      return combined;
    }

    for (final root in children[null] ?? const <AvaCanvasNode>[]) {
      visit(root, Matrix4.identity(), true);
    }

    for (final node in scene.nodes.values) {
      if (node.kind != AvaCanvasNodeKind.group) continue;
      final bounds = descendantBounds[node.id];
      if (bounds == null) continue;
      final json = engine.effectiveNodeJson(node);
      final renderNode = AvaCanvasRenderNode(
        node: node,
        json: json,
        worldTransform: Matrix4.identity(),
        logicalPath: Path()..addRect(bounds),
        logicalBounds: bounds,
        paintOrder: result.length,
        bindings: List.unmodifiable(bindingsByNode[node.id] ?? const []),
      );
      result.add(renderNode);
      byId[node.id] = renderNode;
    }

    return AvaCanvasGeometry._(
      nodes: List.unmodifiable(result),
      byId: Map.unmodifiable(byId),
    );
  }

  AvaCanvasInteractionBinding? hitTest(
    Offset logicalPosition, {
    AvaCanvasInteractionEvent? event,
    double minimumTargetExtent = 44,
  }) {
    final hitOrder = <AvaCanvasRenderNode>[
      ...nodes.reversed.where(
        (renderNode) => renderNode.node.kind != AvaCanvasNodeKind.group,
      ),
      ...nodes.reversed.where(
        (renderNode) => renderNode.node.kind == AvaCanvasNodeKind.group,
      ),
    ];
    for (final renderNode in hitOrder) {
      final candidates = event == null
          ? renderNode.bindings
          : renderNode.bindings
              .where((binding) => binding.event == event)
              .toList(growable: false);
      if (candidates.isEmpty) continue;
      final path = renderNode.logicalPath;
      if (path == null) continue;
      final strokeWidth =
          ((renderNode.json['style']! as Map)['strokeWidth']! as num)
              .toDouble();
      final hitSlop = math.max(10, strokeWidth / 2 + 7).toDouble();
      final expandsSmallTarget =
          renderNode.logicalBounds.width < minimumTargetExtent ||
              renderNode.logicalBounds.height < minimumTargetExtent;
      if (path.contains(logicalPosition) ||
          (expandsSmallTarget &&
              renderNode
                  .interactionBounds(minimumExtent: minimumTargetExtent)
                  .contains(logicalPosition)) ||
          _strokeContains(path, logicalPosition, hitSlop)) {
        return candidates.last;
      }
    }
    return null;
  }

  bool _strokeContains(Path path, Offset point, double tolerance) {
    if (!path.getBounds().inflate(tolerance).contains(point)) return false;
    for (final metric in path.computeMetrics()) {
      if (metric.length == 0) continue;
      final sampleCount = math.min(
        512,
        math.max(1, (metric.length / math.max(4, tolerance / 2)).ceil()),
      );
      var previous = metric.getTangentForOffset(0)?.position;
      if (previous == null) continue;
      for (var index = 1; index <= sampleCount; index += 1) {
        final current = metric
            .getTangentForOffset(metric.length * index / sampleCount)
            ?.position;
        if (current == null) continue;
        final segmentStart = previous;
        if (segmentStart == null) continue;
        if (_distanceToSegment(point, segmentStart, current) <= tolerance) {
          return true;
        }
        previous = current;
      }
    }
    return false;
  }

  double _distanceToSegment(Offset point, Offset start, Offset end) {
    final segment = end - start;
    final lengthSquared = segment.dx * segment.dx + segment.dy * segment.dy;
    if (lengthSquared == 0) return (point - start).distance;
    final relative = point - start;
    final projection =
        ((relative.dx * segment.dx + relative.dy * segment.dy) / lengthSquared)
            .clamp(0.0, 1.0);
    return (point - (start + segment * projection)).distance;
  }
}

void _centerTextInExploreCard(
  Map<String, Object?> textJson,
  Map<String, Object?> cardJson,
) {
  final left = (cardJson['x']! as num).toDouble();
  final top = (cardJson['y']! as num).toDouble();
  final width = (cardJson['width']! as num).toDouble();
  final height = (cardJson['height']! as num).toDouble();
  textJson['_layoutMaxWidth'] = math.max(44, width - 32).toDouble();
  textJson['x'] = left + width / 2;
  var metrics = AvaCanvasNodeGeometry.textMetrics(textJson);
  while (metrics.bounds.height > height - 24 &&
      (textJson['fontSize']! as num).toDouble() > 16) {
    textJson['fontSize'] = (textJson['fontSize']! as num).toDouble() - 1;
    metrics = AvaCanvasNodeGeometry.textMetrics(textJson);
  }
  textJson['y'] = top + (height + metrics.bounds.height) / 2;
}

abstract final class AvaCanvasNodeGeometry {
  static Path localPath(
    AvaCanvasNode node,
    Map<String, Object?> json,
  ) {
    Offset point(Object? raw) {
      final value = (raw! as Map).cast<String, Object?>();
      return Offset(
        (value['x']! as num).toDouble(),
        (value['y']! as num).toDouble(),
      );
    }

    switch (node.kind) {
      case AvaCanvasNodeKind.group:
        return Path();
      case AvaCanvasNodeKind.text:
        final metrics = textMetrics(json);
        return Path()..addRect(metrics.bounds);
      case AvaCanvasNodeKind.line:
      case AvaCanvasNodeKind.arrow:
        final start = point(json['start']);
        final end = point(json['end']);
        final path = Path()
          ..moveTo(start.dx, start.dy)
          ..lineTo(end.dx, end.dy);
        if (node.kind == AvaCanvasNodeKind.arrow) {
          final angle = math.atan2(end.dy - start.dy, end.dx - start.dx);
          const length = 18.0;
          const spread = 0.55;
          path
            ..moveTo(end.dx, end.dy)
            ..lineTo(
              end.dx - length * math.cos(angle - spread),
              end.dy - length * math.sin(angle - spread),
            )
            ..moveTo(end.dx, end.dy)
            ..lineTo(
              end.dx - length * math.cos(angle + spread),
              end.dy - length * math.sin(angle + spread),
            );
        }
        return path;
      case AvaCanvasNodeKind.circle:
        final center = point(json['center']);
        final radius = (json['radius']! as num).toDouble();
        return Path()..addOval(Rect.fromCircle(center: center, radius: radius));
      case AvaCanvasNodeKind.ellipse:
        final center = point(json['center']);
        final radiusX = (json['radiusX']! as num).toDouble();
        final radiusY = (json['radiusY']! as num).toDouble();
        return Path()
          ..addOval(
            Rect.fromCenter(
              center: center,
              width: radiusX * 2,
              height: radiusY * 2,
            ),
          );
      case AvaCanvasNodeKind.rect:
        final rect = Rect.fromLTWH(
          (json['x']! as num).toDouble(),
          (json['y']! as num).toDouble(),
          (json['width']! as num).toDouble(),
          (json['height']! as num).toDouble(),
        );
        final radius = (json['cornerRadius']! as num).toDouble();
        return Path()
          ..addRRect(
            RRect.fromRectAndRadius(rect, Radius.circular(radius)),
          );
      case AvaCanvasNodeKind.polygon:
      case AvaCanvasNodeKind.polyline:
        final points = (json['points']! as List).map(point).toList();
        final path = Path()..moveTo(points.first.dx, points.first.dy);
        for (final value in points.skip(1)) {
          path.lineTo(value.dx, value.dy);
        }
        if (node.kind == AvaCanvasNodeKind.polygon) path.close();
        return path;
      case AvaCanvasNodeKind.path:
        return AvaCanvasPathParser.parse(json['pathData']! as String);
    }
  }

  static AvaCanvasTextMetrics textMetrics(Map<String, Object?> json) {
    final text = json['text']! as String;
    final fontSize = (json['fontSize']! as num).toDouble();
    final fontWeight = switch (json['fontWeight']! as String) {
      'regular' => FontWeight.w400,
      'medium' => FontWeight.w600,
      'bold' => FontWeight.w700,
      _ => throw StateError('Unvalidated canvas font weight.'),
    };
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(fontSize: fontSize, fontWeight: fontWeight),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 4,
    )..layout(
        maxWidth: (json['_layoutMaxWidth'] as num?)?.toDouble() ?? 900,
      );
    final x = (json['x']! as num).toDouble();
    final y = (json['y']! as num).toDouble();
    final left = switch (json['textAnchor']! as String) {
      'start' => x,
      'middle' => x - painter.width / 2,
      'end' => x - painter.width,
      _ => throw StateError('Unvalidated canvas text anchor.'),
    };
    final origin = Offset(left, y - painter.height);
    return AvaCanvasTextMetrics(
      painter: painter,
      origin: origin,
      bounds: origin & painter.size,
    );
  }
}

final class AvaCanvasTextMetrics {
  const AvaCanvasTextMetrics({
    required this.painter,
    required this.origin,
    required this.bounds,
  });

  final TextPainter painter;
  final Offset origin;
  final Rect bounds;
}

Matrix4 _nodeTransform(Map<dynamic, dynamic> raw) {
  final transform = raw.cast<String, Object?>();
  final translateX = (transform['translateX']! as num).toDouble();
  final translateY = (transform['translateY']! as num).toDouble();
  final scale = (transform['scale']! as num).toDouble();
  final rotation =
      (transform['rotationDegrees']! as num).toDouble() * math.pi / 180;
  final pivotX = (transform['pivotX'] as num?)?.toDouble() ?? 0;
  final pivotY = (transform['pivotY'] as num?)?.toDouble() ?? 0;
  return Matrix4.identity()
    ..translateByDouble(translateX, translateY, 0, 1)
    ..translateByDouble(pivotX, pivotY, 0, 1)
    ..rotateZ(rotation)
    ..scaleByDouble(scale, scale, 1, 1)
    ..translateByDouble(-pivotX, -pivotY, 0, 1);
}
