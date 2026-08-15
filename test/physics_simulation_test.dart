import 'package:flutter_test/flutter_test.dart';
import 'package:ava/models/whiteboard_item.dart';

void main() {
  group('Whiteboard Physics Sandbox & Bounding Box Tests', () {
    test('Bounding Box is Correct for Circles', () {
      const circle = WhiteboardItem(
        id: 'circle_1',
        type: WhiteboardItemType.circle,
        x: 100.0,
        y: 100.0,
        radius: 40.0,
      );

      final box = circle.getBoundingBox();
      expect(box, equals([60.0, 60.0, 140.0, 140.0]));
    });

    test('Bounding Box is Correct for Rectangles', () {
      const rect = WhiteboardItem(
        id: 'rect_1',
        type: WhiteboardItemType.rectangle,
        x: 150.0,
        y: 150.0,
        width: 80.0,
        height: 60.0,
        angle: 0.0, // AABB aligned
      );

      final box = rect.getBoundingBox();
      expect(box, equals([110.0, 120.0, 190.0, 180.0]));
    });

    test('Hit Testing containsPoint Works Correctly', () {
      const circle = WhiteboardItem(
        id: 'circle_1',
        type: WhiteboardItemType.circle,
        x: 100.0,
        y: 100.0,
        radius: 30.0,
      );

      expect(circle.containsPoint(100.0, 100.0), isTrue); // Center
      expect(circle.containsPoint(120.0, 100.0), isTrue); // Inside right edge
      expect(circle.containsPoint(140.0, 100.0), isFalse); // Outside right edge
    });
  });
}
