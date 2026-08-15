import 'dart:math' as math;

enum WhiteboardItemType { circle, rectangle, drop }

class WhiteboardItem {
  final String id;
  final WhiteboardItemType type;
  final double x;
  final double y;
  final double vx;
  final double vy;
  final double angle;
  final double angularVelocity;
  final double radius; // For circles/drops
  final double width; // For rectangles
  final double height; // For rectangles
  final double mass;
  final int colorValue; // ARGB hex value
  final String label;
  final bool isDragging;

  const WhiteboardItem({
    required this.id,
    required this.type,
    required this.x,
    required this.y,
    this.vx = 0.0,
    this.vy = 0.0,
    this.angle = 0.0,
    this.angularVelocity = 0.0,
    this.radius = 40.0,
    this.width = 80.0,
    this.height = 80.0,
    this.mass = 1.0,
    this.colorValue = 0xFF4A90E2, // Default sky blue
    this.label = '',
    this.isDragging = false,
  });

  /// Check if a point (e.g. touch coordinate) is within this item's bounds
  bool containsPoint(double px, double py) {
    if (type == WhiteboardItemType.circle || type == WhiteboardItemType.drop) {
      final dx = px - x;
      final dy = py - y;
      return math.sqrt(dx * dx + dy * dy) <= radius;
    } else if (type == WhiteboardItemType.rectangle) {
      // Basic rotatable rectangle hit test
      final cosA = math.cos(-angle);
      final sinA = math.sin(-angle);
      // Translate to origin and rotate back
      final tx = px - x;
      final ty = py - y;
      final rx = tx * cosA - ty * sinA;
      final ry = tx * sinA + ty * cosA;
      return rx.abs() <= width / 2 && ry.abs() <= height / 2;
    }
    return false;
  }

  /// Calculates the bounding box for label positioning calculations
  /// Returns [left, top, right, bottom]
  List<double> getBoundingBox() {
    if (type == WhiteboardItemType.circle || type == WhiteboardItemType.drop) {
      return [x - radius, y - radius, x + radius, y + radius];
    } else {
      // Conservative axis-aligned bounding box for rotated rectangle
      final cosA = math.cos(angle).abs();
      final sinA = math.sin(angle).abs();
      final halfW = (width * cosA + height * sinA) / 2;
      final halfH = (width * sinA + height * cosA) / 2;
      return [x - halfW, y - halfH, x + halfW, y + halfH];
    }
  }

  WhiteboardItem copyWith({
    String? id,
    WhiteboardItemType? type,
    double? x,
    double? y,
    double? vx,
    double? vy,
    double? angle,
    double? angularVelocity,
    double? radius,
    double? width,
    double? height,
    double? mass,
    int? colorValue,
    String? label,
    bool? isDragging,
  }) {
    return WhiteboardItem(
      id: id ?? this.id,
      type: type ?? this.type,
      x: x ?? this.x,
      y: y ?? this.y,
      vx: vx ?? this.vx,
      vy: vy ?? this.vy,
      angle: angle ?? this.angle,
      angularVelocity: angularVelocity ?? this.angularVelocity,
      radius: radius ?? this.radius,
      width: width ?? this.width,
      height: height ?? this.height,
      mass: mass ?? this.mass,
      colorValue: colorValue ?? this.colorValue,
      label: label ?? this.label,
      isDragging: isDragging ?? this.isDragging,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'type': type.name,
      'x': x,
      'y': y,
      'vx': vx,
      'vy': vy,
      'angle': angle,
      'angularVelocity': angularVelocity,
      'radius': radius,
      'width': width,
      'height': height,
      'mass': mass,
      'colorValue': colorValue,
      'label': label,
    };
  }

  factory WhiteboardItem.fromJson(Map<String, dynamic> json) {
    return WhiteboardItem(
      id: json['id'] as String,
      type: WhiteboardItemType.values.firstWhere(
        (e) => e.name == json['type'],
        orElse: () => WhiteboardItemType.circle,
      ),
      x: (json['x'] as num).toDouble(),
      y: (json['y'] as num).toDouble(),
      vx: (json['vx'] ?? 0.0 as num).toDouble(),
      vy: (json['vy'] ?? 0.0 as num).toDouble(),
      angle: (json['angle'] ?? 0.0 as num).toDouble(),
      angularVelocity: (json['angularVelocity'] ?? 0.0 as num).toDouble(),
      radius: (json['radius'] ?? 40.0 as num).toDouble(),
      width: (json['width'] ?? 80.0 as num).toDouble(),
      height: (json['height'] ?? 80.0 as num).toDouble(),
      mass: (json['mass'] ?? 1.0 as num).toDouble(),
      colorValue: json['colorValue'] as int? ?? 0xFF4A90E2,
      label: json['label'] as String? ?? '',
      isDragging: false,
    );
  }
}
