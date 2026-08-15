import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/ava_filament_viewer.dart';

void main() {
  test('converts logical touch coordinates into Metal texture pixels', () {
    expect(
      avaFilamentPhysicalPickPixel(const Offset(120.25, 310.5), 2),
      const math.Point<int>(241, 621),
    );
    expect(
      avaFilamentPhysicalPickPixel(const Offset(120.25, 310.5), 1),
      const math.Point<int>(120, 311),
    );
  });

  test('rejects invalid texture coordinate inputs', () {
    expect(
      () => avaFilamentPhysicalPickPixel(const Offset(1, 1), 0),
      throwsArgumentError,
    );
    expect(
      () => avaFilamentPhysicalPickPixel(
        const Offset(double.nan, 1),
        2,
      ),
      throwsArgumentError,
    );
  });

  test('horizontal mesh drag produces a wrapped camera orbit', () {
    expect(avaFilamentOrbitThetaForDrag(0, 100, 1), -32);
    expect(avaFilamentOrbitThetaForDrag(175, -100, 1), -153);
    expect(avaFilamentOrbitThetaForDrag(-175, 100, 1), 153);
  });

  test('vertical mesh drag stays inside the restrained polar range', () {
    expect(
      avaFilamentOrbitPhiForDrag(
        77,
        25,
        1,
        minimumPhiDegrees: 66,
        maximumPhiDegrees: 92,
      ),
      71.5,
    );
    expect(
      avaFilamentOrbitPhiForDrag(
        77,
        -200,
        1,
        minimumPhiDegrees: 66,
        maximumPhiDegrees: 92,
      ),
      92,
    );
    expect(
      avaFilamentOrbitPhiForDrag(
        77,
        200,
        1,
        minimumPhiDegrees: 66,
        maximumPhiDegrees: 92,
      ),
      66,
    );
  });

  test('camera recovery blends both axes to the exact front orbit', () {
    const start = (theta: 156.0, phi: 90.0, radius: 2.3);
    const front = (theta: 0.0, phi: 77.0, radius: 2.18);

    expect(avaFilamentCameraRecoveryOrbit(start, front, 0), start);
    final midway = avaFilamentCameraRecoveryOrbit(start, front, 0.5);
    expect(midway.theta.abs(), lessThan(start.theta.abs()));
    expect(midway.phi, inExclusiveRange(77, 90));
    expect(midway.radius, inExclusiveRange(2.18, 2.3));
    expect(avaFilamentCameraRecoveryOrbit(start, front, 1), front);
  });

  test('rejects invalid camera drag inputs', () {
    expect(
      () => avaFilamentOrbitThetaForDrag(double.nan, 10, 1),
      throwsArgumentError,
    );
    expect(
      () => avaFilamentOrbitThetaForDrag(0, 10, 0),
      throwsArgumentError,
    );
    expect(
      () => avaFilamentOrbitPhiForDrag(
        82,
        10,
        1,
        minimumPhiDegrees: 92,
        maximumPhiDegrees: 72,
      ),
      throwsArgumentError,
    );
    expect(
      () => avaFilamentCameraRecoveryOrbit(
        const (theta: 0.0, phi: 82.0, radius: 2.12),
        const (theta: 0.0, phi: 82.0, radius: 2.12),
        1.1,
      ),
      throwsArgumentError,
    );
  });
}
