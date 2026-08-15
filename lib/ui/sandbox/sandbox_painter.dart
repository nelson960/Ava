import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../models/whiteboard_item.dart';

class SandboxPainter extends CustomPainter {
  final List<WhiteboardItem> items;

  SandboxPainter({required this.items});

  @override
  void paint(Canvas canvas, Size size) {
    // Draw whiteboard grid background to make it look like a technical/playful sandbox
    _drawGridBackground(canvas, size);

    // Draw all items
    for (final item in items) {
      canvas.save();

      // Translate and rotate individual shapes
      canvas.translate(item.x, item.y);
      canvas.rotate(item.angle);

      final paint = Paint()
        ..style = PaintingStyle.fill
        ..isAntiAlias = true;

      // Draw item shadow (layering)
      _drawShapeShadow(canvas, item);

      // Draw the shape body
      _drawShapeBody(canvas, paint, item);

      // Draw a subtle border
      _drawShapeBorder(canvas, item);

      canvas.restore();

      // Draw labels AFTER restoring so labels are NOT rotated and stay perfectly upright/readable!
      _drawLabel(canvas, size, item);
    }
  }

  void _drawGridBackground(Canvas canvas, Size size) {
    final gridPaint = Paint()
      ..color = const Color(0xFFE2E8F0)
      ..strokeWidth = 1.0;

    const double gridSize = 40.0;
    for (double x = 0; x < size.width; x += gridSize) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), gridPaint);
    }
    for (double y = 0; y < size.height; y += gridSize) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }
  }

  void _drawShapeShadow(Canvas canvas, WhiteboardItem item) {
    final shadowPaint = Paint()
      ..color = Colors.black.withValues(alpha: 0.08)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 6);

    final offset = const Offset(0, 4);

    if (item.type == WhiteboardItemType.circle) {
      canvas.drawCircle(offset, item.radius, shadowPaint);
    } else if (item.type == WhiteboardItemType.rectangle) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(
              center: offset, width: item.width, height: item.height),
          const Radius.circular(12),
        ),
        shadowPaint,
      );
    } else if (item.type == WhiteboardItemType.drop) {
      final dropPath = _getDropPath(offset.dx, offset.dy, item.radius);
      canvas.drawPath(dropPath, shadowPaint);
    }
  }

  void _drawShapeBody(Canvas canvas, Paint paint, WhiteboardItem item) {
    // Generate beautiful gradient color palettes
    final baseColor = Color(item.colorValue);
    final lightColor = _getLighterColor(baseColor, 0.25);
    final darkColor = _getDarkerColor(baseColor, 0.15);

    if (item.type == WhiteboardItemType.circle) {
      paint.shader = RadialGradient(
        colors: [lightColor, baseColor, darkColor],
        center: const Alignment(-0.3, -0.3),
        radius: 0.95,
      ).createShader(Rect.fromCircle(center: Offset.zero, radius: item.radius));

      canvas.drawCircle(Offset.zero, item.radius, paint);

      // Sparkly visual bulb reflect
      final reflectionPaint = Paint()
        ..color = Colors.white.withValues(alpha: 0.4)
        ..style = PaintingStyle.fill;
      canvas.drawOval(
        Rect.fromLTWH(-item.radius * 0.5, -item.radius * 0.5, item.radius * 0.4,
            item.radius * 0.25),
        reflectionPaint,
      );
    } else if (item.type == WhiteboardItemType.rectangle) {
      paint.shader = LinearGradient(
        colors: [lightColor, baseColor, darkColor],
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
      ).createShader(Rect.fromCenter(
          center: Offset.zero, width: item.width, height: item.height));

      final rect = Rect.fromCenter(
          center: Offset.zero, width: item.width, height: item.height);
      canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(12)), paint);

      // Inner highlight line
      final highlightPaint = Paint()
        ..color = Colors.white.withValues(alpha: 0.2)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.0;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          rect.deflate(3),
          const Radius.circular(9),
        ),
        highlightPaint,
      );
    } else if (item.type == WhiteboardItemType.drop) {
      // Shaded liquid droplet
      paint.shader = LinearGradient(
        colors: [lightColor, baseColor, darkColor],
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
      ).createShader(Rect.fromCircle(center: Offset.zero, radius: item.radius));

      final dropPath = _getDropPath(0.0, 0.0, item.radius);
      canvas.drawPath(dropPath, paint);

      // Liquid reflection highlights
      final highlightPaint = Paint()
        ..color = Colors.white.withValues(alpha: 0.35)
        ..style = PaintingStyle.fill;

      final refPath = Path()
        ..moveTo(-item.radius * 0.35, item.radius * 0.1)
        ..quadraticBezierTo(
            -item.radius * 0.35, -item.radius * 0.3, 0.0, -item.radius * 0.5)
        ..quadraticBezierTo(-item.radius * 0.1, -item.radius * 0.5,
            -item.radius * 0.25, -item.radius * 0.1)
        ..close();
      canvas.drawPath(refPath, highlightPaint);
    }
  }

  void _drawShapeBorder(Canvas canvas, WhiteboardItem item) {
    final borderPaint = Paint()
      ..color = const Color(0xFF1E293B).withValues(alpha: 0.15)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5;

    if (item.type == WhiteboardItemType.circle) {
      canvas.drawCircle(Offset.zero, item.radius, borderPaint);
    } else if (item.type == WhiteboardItemType.rectangle) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(
              center: Offset.zero, width: item.width, height: item.height),
          const Radius.circular(12),
        ),
        borderPaint,
      );
    } else if (item.type == WhiteboardItemType.drop) {
      canvas.drawPath(_getDropPath(0.0, 0.0, item.radius), borderPaint);
    }
  }

  /// Draw bounding text label safely above or below the shape
  void _drawLabel(Canvas canvas, Size size, WhiteboardItem item) {
    if (item.label.isEmpty) return;

    final box = item.getBoundingBox();
    final double left = box[0];
    final double top = box[1];
    final double right = box[2];
    final double bottom = box[3];

    // Text configuration
    final textPainter = TextPainter(
      text: TextSpan(
        text: item.label,
        style: const TextStyle(
          color: Colors.white,
          fontSize: 13.0,
          fontWeight: FontWeight.bold,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();

    // Auto-calculate position: standard is above the shape.
    // If too close to the top boundary, display below the shape.
    final double labelPadding = 14.0;
    double labelX = (left + right) / 2 - textPainter.width / 2;
    double labelY = top - textPainter.height - labelPadding;

    if (labelY < labelPadding) {
      labelY = bottom + labelPadding;
    }

    // Clamp coordinates to prevent clipping on the left/right screen edges
    labelX = labelX.clamp(
        labelPadding, size.width - textPainter.width - labelPadding);
    labelY = labelY.clamp(
        labelPadding, size.height - textPainter.height - labelPadding);

    // Draw capsule background for text readability (high contrast)
    final capsulePaint = Paint()
      ..color = const Color(0xFF1E293B).withValues(alpha: 0.7)
      ..style = PaintingStyle.fill;

    final capsuleRect = RRect.fromRectAndRadius(
      Rect.fromLTWH(
        labelX - 8,
        labelY - 4,
        textPainter.width + 16,
        textPainter.height + 8,
      ),
      const Radius.circular(20),
    );

    canvas.drawRRect(capsuleRect, capsulePaint);

    // Draw white caption text
    textPainter.paint(canvas, Offset(labelX, labelY));
  }

  Path _getDropPath(double cx, double cy, double radius) {
    final path = Path()
      ..moveTo(cx, cy - radius)
      ..cubicTo(
        cx + radius * 0.75,
        cy - radius * 0.1,
        cx + radius,
        cy + radius * 0.35,
        cx + radius,
        cy + radius * 0.55,
      )
      ..arcToPoint(
        Offset(cx - radius, cy + radius * 0.55),
        radius: Radius.circular(radius),
        clockwise: true,
      )
      ..cubicTo(
        cx - radius,
        cy + radius * 0.35,
        cx - radius * 0.75,
        cy - radius * 0.1,
        cx,
        cy - radius,
      )
      ..close();
    return path;
  }

  // --- Helper Color Utilities ---

  Color _getLighterColor(Color c, double factor) {
    return c.withValues(
      red: math.min(1.0, c.r + (1.0 - c.r) * factor),
      green: math.min(1.0, c.g + (1.0 - c.g) * factor),
      blue: math.min(1.0, c.b + (1.0 - c.b) * factor),
    );
  }

  Color _getDarkerColor(Color c, double factor) {
    return c.withValues(
      red: math.max(0.0, c.r - c.r * factor),
      green: math.max(0.0, c.g - c.g * factor),
      blue: math.max(0.0, c.b - c.b * factor),
    );
  }

  @override
  bool shouldRepaint(covariant SandboxPainter oldDelegate) {
    return oldDelegate.items != items;
  }
}
