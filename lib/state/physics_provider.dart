import 'dart:math' as math;
import 'package:flutter/scheduler.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/whiteboard_item.dart';

class PhysicsSandboxState {
  final List<WhiteboardItem> items;
  final double width;
  final double height;

  const PhysicsSandboxState({
    required this.items,
    this.width = 1024.0, // Virtual coordinate width
    this.height = 600.0, // Virtual coordinate height
  });

  PhysicsSandboxState copyWith({
    List<WhiteboardItem>? items,
    double? width,
    double? height,
  }) {
    return PhysicsSandboxState(
      items: items ?? this.items,
      width: width ?? this.width,
      height: height ?? this.height,
    );
  }
}

class PhysicsSandboxNotifier extends StateNotifier<PhysicsSandboxState>
    implements TickerProvider {
  Ticker? _ticker;
  Duration _lastElapsed = Duration.zero;

  // Physics constants
  static const double gravity = 400.0; // pixels / s^2
  static const double damping = 0.35; // Air resistance
  static const double restitution = 0.75; // Bounciness
  static const double friction = 0.98; // Rotational damping

  PhysicsSandboxNotifier() : super(const PhysicsSandboxState(items: [])) {
    // Start physics simulation loop
    _ticker = createTicker(_tick)..start();
  }

  @override
  Ticker createTicker(TickerCallback onTick) => Ticker(onTick);

  @override
  void dispose() {
    _ticker?.dispose();
    super.dispose();
  }

  void setDimensions(double w, double h) {
    state = state.copyWith(width: w, height: h);
  }

  /// Simulation step called on every vsync frame (60/120 Hz)
  void _tick(Duration elapsed) {
    if (_lastElapsed == Duration.zero) {
      _lastElapsed = elapsed;
      return;
    }

    double dt =
        (elapsed.inMicroseconds - _lastElapsed.inMicroseconds) / 1000000.0;
    _lastElapsed = elapsed;

    // Cap dt to prevent numerical explosion during backgrounding
    if (dt > 0.05) dt = 0.05;

    // Run multiple mini-steps for collision stability
    const int substeps = 2;
    final double subDt = dt / substeps;

    List<WhiteboardItem> updated = List.from(state.items);

    for (int step = 0; step < substeps; step++) {
      // 1. Integrate forces and velocities
      for (int i = 0; i < updated.length; i++) {
        final item = updated[i];
        if (item.isDragging) continue;

        // Apply gravity
        double vy = item.vy + gravity * subDt;
        double vx = item.vx;

        // Apply air resistance
        vx *= (1.0 - damping * subDt);
        vy *= (1.0 - damping * subDt);

        // Update positions
        double x = item.x + vx * subDt;
        double y = item.y + vy * subDt;

        // Update rotation
        double angle = item.angle + item.angularVelocity * subDt;
        double angularVel = item.angularVelocity * (1.0 - friction * subDt);

        updated[i] = item.copyWith(
          x: x,
          y: y,
          vx: vx,
          vy: vy,
          angle: angle,
          angularVelocity: angularVel,
        );
      }

      // 2. Resolve object-to-object collisions
      _resolveCollisions(updated);

      // 3. Resolve boundary constraints
      _resolveBoundaries(updated);
    }

    state = state.copyWith(items: updated);
  }

  /// Resolve boundary collisions (restitution bounce off whiteboard walls)
  void _resolveBoundaries(List<WhiteboardItem> list) {
    final w = state.width;
    final h = state.height;

    for (int i = 0; i < list.length; i++) {
      final item = list[i];
      if (item.isDragging) continue;

      double x = item.x;
      double y = item.y;
      double vx = item.vx;
      double vy = item.vy;
      double angularVel = item.angularVelocity;

      if (item.type == WhiteboardItemType.circle ||
          item.type == WhiteboardItemType.drop) {
        final r = item.radius;

        // Bottom
        if (y + r > h) {
          y = h - r;
          vy = -vy * restitution;
          // Roll friction translation (slipping model)
          angularVel = vx / r;
          vx *= 0.9;
        }
        // Top
        else if (y - r < 0) {
          y = r;
          vy = -vy * restitution;
        }

        // Left
        if (x - r < 0) {
          x = r;
          vx = -vx * restitution;
        }
        // Right
        else if (x + r > w) {
          x = w - r;
          vx = -vx * restitution;
        }
      } else {
        // Rectangle boundary check (simplified AABB representation for speed)
        final hw = item.width / 2;
        final hh = item.height / 2;

        if (y + hh > h) {
          y = h - hh;
          vy = -vy * restitution;
          vx *= 0.9;
        } else if (y - hh < 0) {
          y = hh;
          vy = -vy * restitution;
        }

        if (x - hw < 0) {
          x = hw;
          vx = -vx * restitution;
        } else if (x + hw > w) {
          x = w - hw;
          vx = -vx * restitution;
        }
      }

      list[i] = item.copyWith(
          x: x, y: y, vx: vx, vy: vy, angularVelocity: angularVel);
    }
  }

  /// Resolve simple elastic collisions between circles & squares
  void _resolveCollisions(List<WhiteboardItem> list) {
    for (int i = 0; i < list.length; i++) {
      for (int j = i + 1; j < list.length; j++) {
        final a = list[i];
        final b = list[j];

        // Approximate everything as a bounding sphere for physics solver stability
        final double rA =
            a.type == WhiteboardItemType.rectangle ? a.width / 2 : a.radius;
        final double rB =
            b.type == WhiteboardItemType.rectangle ? b.width / 2 : b.radius;

        final double dx = b.x - a.x;
        final double dy = b.y - a.y;
        final double dist = math.sqrt(dx * dx + dy * dy);
        final double minDist = rA + rB;

        if (dist < minDist) {
          // Normal collision vector
          final double nx = dist == 0 ? 1.0 : dx / dist;
          final double ny = dist == 0 ? 0.0 : dy / dist;

          // Resolve overlap (penetration resolution)
          final double overlap = minDist - dist;
          final double separationX = nx * overlap * 0.5;
          final double separationY = ny * overlap * 0.5;

          double ax = a.x;
          double ay = a.y;
          double bx = b.x;
          double by = b.y;

          if (!a.isDragging) {
            ax -= separationX;
            ay -= separationY;
          }
          if (!b.isDragging) {
            bx += separationX;
            by += separationY;
          }

          // Relative velocity
          final double rvx = b.vx - a.vx;
          final double rvy = b.vy - a.vy;

          // Velocity along normal
          final double velAlongNormal = rvx * nx + rvy * ny;

          // Do not resolve if velocities are separating
          if (velAlongNormal < 0) {
            // Calculate impulse scalar
            final double impulseScalar = -(1.0 + restitution) *
                velAlongNormal /
                (1.0 / a.mass + 1.0 / b.mass);

            double avx = a.vx;
            double avy = a.vy;
            double bvx = b.vx;
            double bvy = b.vy;

            if (!a.isDragging) {
              avx -= nx * impulseScalar / a.mass;
              avy -= ny * impulseScalar / a.mass;
            }
            if (!b.isDragging) {
              bvx += nx * impulseScalar / b.mass;
              bvy += ny * impulseScalar / b.mass;
            }

            // Transfer slight angular torque from elastic slide
            final double tangentX = -ny;
            final double tangentY = nx;
            final double tangentVel = rvx * tangentX + rvy * tangentY;

            double aAng = a.angularVelocity + (tangentVel * 0.05 / a.mass);
            double bAng = b.angularVelocity - (tangentVel * 0.05 / b.mass);

            list[i] = a.copyWith(
                x: ax, y: ay, vx: avx, vy: avy, angularVelocity: aAng);
            list[j] = b.copyWith(
                x: bx, y: by, vx: bvx, vy: bvy, angularVelocity: bAng);
          } else {
            list[i] = a.copyWith(x: ax, y: ay);
            list[j] = b.copyWith(x: bx, y: by);
          }
        }
      }
    }
  }

  // --- Interaction API ---

  /// Trigger drag state
  void startDragging(String id, double touchX, double touchY) {
    state = state.copyWith(
      items: state.items.map((item) {
        if (item.id == id) {
          return item.copyWith(
            isDragging: true,
            x: touchX,
            y: touchY,
            vx: 0.0,
            vy: 0.0,
          );
        }
        return item;
      }).toList(),
    );
  }

  /// Update position on drag
  void updateDragging(
      String id, double touchX, double touchY, double vx, double vy) {
    state = state.copyWith(
      items: state.items.map((item) {
        if (item.id == id) {
          return item.copyWith(
            x: touchX,
            y: touchY,
            vx: vx,
            vy: vy,
          );
        }
        return item;
      }).toList(),
    );
  }

  /// Release item, applying velocities
  void stopDragging(String id, double releaseVx, double releaseVy) {
    state = state.copyWith(
      items: state.items.map((item) {
        if (item.id == id) {
          return item.copyWith(
            isDragging: false,
            vx: releaseVx,
            vy: releaseVy,
          );
        }
        return item;
      }).toList(),
    );
  }

  /// Spin item on double tap or rotation gestures
  void spinItem(String id, double torque) {
    state = state.copyWith(
      items: state.items.map((item) {
        if (item.id == id) {
          return item.copyWith(
            angularVelocity: item.angularVelocity + torque,
          );
        }
        return item;
      }).toList(),
    );
  }

  /// Spawn educational items from socket packet streams
  void spawnItem(WhiteboardItem item) {
    state = state.copyWith(
      items: [...state.items, item],
    );
  }

  /// Clear whiteboard
  void clearBoard() {
    state = state.copyWith(items: []);
  }
}

final physicsSandboxProvider =
    StateNotifierProvider<PhysicsSandboxNotifier, PhysicsSandboxState>((ref) {
  return PhysicsSandboxNotifier();
});
