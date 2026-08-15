import 'dart:math' as math;
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('Rigged Avatar Spring Physics Math Tests', () {
    test('Damped Harmonic Oscillator - Amplitude Decreases', () {
      // Spring properties
      const double m = 1.0; // mass
      const double k = 200.0; // stiffness
      const double c = 10.0; // damping coefficient

      double x = 100.0; // initial displacement
      double v = 0.0; // initial velocity
      const double dt = 0.016; // 60Hz frame time

      // Run simulation for 20 frames (approx 0.3s)
      double maxDisplacementAfterInitial = 0.0;
      for (int i = 0; i < 20; i++) {
        double force = -k * x - c * v;
        double accel = force / m;
        v += accel * dt;
        x += v * dt;

        if (x.abs() > maxDisplacementAfterInitial && i > 1) {
          maxDisplacementAfterInitial = x.abs();
        }
      }

      // Assert that damping has caused the displacement amplitude to decrease below the initial value of 100
      expect(x.abs(), lessThan(100.0));
    });
  });

  group('Avatar Arm Pointing Inverse Kinematics Tests', () {
    test('Law of Cosines Angle Computation is Mathematically Valid', () {
      // Coordinates of shoulder and target (relative to shoulder)
      const double targetX = 80.0;
      const double targetY = 60.0;

      // Bone segment lengths
      const double l1 = 65.0;
      const double l2 = 65.0;

      final double dist = math.sqrt(targetX * targetX + targetY * targetY);

      // Law of cosines: cos(theta2) = (d^2 - l1^2 - l2^2) / (2 * l1 * l2)
      final double cosTheta2 =
          (dist * dist - l1 * l1 - l2 * l2) / (2 * l1 * l2);

      expect(cosTheta2, greaterThanOrEqualTo(-1.0));
      expect(cosTheta2, lessThanOrEqualTo(1.0));

      final double theta2 = math.acos(cosTheta2);
      expect(theta2, isNot(double.nan));
    });
  });
}
