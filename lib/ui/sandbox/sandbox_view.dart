import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'sandbox_painter.dart';
import '../../models/whiteboard_item.dart';
import '../../state/physics_provider.dart';

class SandboxView extends ConsumerStatefulWidget {
  const SandboxView({super.key});

  @override
  ConsumerState<SandboxView> createState() => _SandboxViewState();
}

class _SandboxViewState extends ConsumerState<SandboxView> {
  String? _draggedItemId;
  double _lastX = 0.0;
  double _lastY = 0.0;
  DateTime? _lastTime;

  // Track sliding average velocities during drag
  double _velX = 0.0;
  double _velY = 0.0;

  @override
  Widget build(BuildContext context) {
    final physicsState = ref.watch(physicsSandboxProvider);
    final physicsNotifier = ref.read(physicsSandboxProvider.notifier);

    return LayoutBuilder(
      builder: (context, constraints) {
        // Feed real width and height to the physical engine boundary checks
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (physicsState.width != constraints.maxWidth ||
              physicsState.height != constraints.maxHeight) {
            physicsNotifier.setDimensions(
                constraints.maxWidth, constraints.maxHeight);
          }
        });

        return GestureDetector(
          behavior: HitTestBehavior.opaque,

          // Tap to detect object
          onPanDown: (details) {
            final localPos = details.localPosition;
            final hitItem =
                _findHitItem(localPos.dx, localPos.dy, physicsState.items);

            if (hitItem != null) {
              _draggedItemId = hitItem.id;
              _lastX = localPos.dx;
              _lastY = localPos.dy;
              _lastTime = DateTime.now();
              _velX = 0.0;
              _velY = 0.0;

              physicsNotifier.startDragging(
                  _draggedItemId!, localPos.dx, localPos.dy);
            }
          },

          // Drag update with velocity tracker
          onPanUpdate: (details) {
            if (_draggedItemId == null) return;

            final localPos = details.localPosition;
            final now = DateTime.now();

            if (_lastTime != null) {
              final double dt =
                  now.difference(_lastTime!).inMicroseconds / 1000000.0;
              if (dt > 0.001) {
                final double instantVx = (localPos.dx - _lastX) / dt;
                final double instantVy = (localPos.dy - _lastY) / dt;

                // Low-pass filter for smooth velocity releases
                _velX = _velX * 0.4 + instantVx * 0.6;
                _velY = _velY * 0.4 + instantVy * 0.6;
              }
            }

            _lastX = localPos.dx;
            _lastY = localPos.dy;
            _lastTime = now;

            physicsNotifier.updateDragging(
                _draggedItemId!, localPos.dx, localPos.dy, _velX, _velY);
          },

          // Drag release - applies inertia velocity to object
          onPanEnd: (details) {
            if (_draggedItemId == null) return;

            // Limit release velocities to avoid items flying off-screen at infinite speed
            final double limit = 1500.0;
            final double finalVx = _velX.clamp(-limit, limit);
            final double finalVy = _velY.clamp(-limit, limit);

            physicsNotifier.stopDragging(_draggedItemId!, finalVx, finalVy);
            _draggedItemId = null;
          },

          onPanCancel: () {
            if (_draggedItemId == null) return;
            physicsNotifier.stopDragging(_draggedItemId!, 0.0, 0.0);
            _draggedItemId = null;
          },

          // Double tap triggers spin torque for playfulness
          onDoubleTapDown: (details) {
            final localPos = details.localPosition;
            final hitItem =
                _findHitItem(localPos.dx, localPos.dy, physicsState.items);
            if (hitItem != null) {
              // Apply spin torque (positive or negative depending on side clicked)
              final isLeft = localPos.dx < hitItem.x;
              final double torque = isLeft ? 12.0 : -12.0;
              physicsNotifier.spinItem(hitItem.id, torque);
            }
          },

          child: CustomPaint(
            size: Size.infinite,
            painter: SandboxPainter(items: physicsState.items),
          ),
        );
      },
    );
  }

  WhiteboardItem? _findHitItem(
      double px, double py, List<WhiteboardItem> items) {
    // Search backwards (last item in list is drawn on top)
    for (int i = items.length - 1; i >= 0; i--) {
      if (items[i].containsPoint(px, py)) {
        return items[i];
      }
    }
    return null;
  }
}
