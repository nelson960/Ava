import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/ava_3d_viewer.dart';

void main() {
  test('camera interaction defaults recover the front view after inactivity',
      () {
    const configuration = AvaViewerGestureConfiguration();

    expect(configuration.autoCenterDelay, const Duration(seconds: 30));
    expect(configuration.frontPolarAngleDegrees, 82);
    expect(configuration.interpolationDecay, greaterThan(0));
  });

  test('decodes repeated native WebView pointer events', () {
    final events = <AvaViewerPointerEvent>[];

    for (var index = 0; index < 30; index += 1) {
      events.add(
        AvaViewerPointerEvent.fromChannelArguments([
          80 + index,
          120 + index * 2,
          400,
          800,
          0.12,
          1.3,
          -0.04,
          0,
          0,
          1,
          '0 0 10 11 12 0.2 0.3 0.5',
          140,
          0.5,
          'touch',
          1.25,
          1.43,
          1.9,
          0,
        ]),
      );
    }

    expect(events, hasLength(30));
    expect(events.first.position.dx, 80);
    expect(events.last.position.dy, 178);
    expect(events.first.modelPosition.y, 1.3);
    expect(events.first.surface.meshIndex, 0);
    expect(events.first.contactDuration, const Duration(milliseconds: 140));
    expect(events.first.pointerType, 'touch');
    expect(events.first.cameraAzimuth, 1.25);
    expect(events.first.cameraElevation, 1.43);
    expect(events.first.cameraRadius, 1.9);
    expect(events.first.avatarRootYaw, 0);
    expect(
        events,
        everyElement(
          isA<AvaViewerPointerEvent>().having(
            (event) => event.viewportSize,
            'viewportSize',
            const Size(400, 800),
          ),
        ));
  });

  test('rejects malformed native pointer data', () {
    final valid = <dynamic>[
      10,
      20,
      400,
      800,
      0.12,
      1.3,
      -0.04,
      0,
      0,
      1,
      '0 0 10 11 12 0.2 0.3 0.5',
      140,
      0.5,
      'touch',
      1.25,
      1.43,
      1.9,
      0,
    ];
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments(
        [...valid]..[2] = 0,
      ),
      throwsFormatException,
    );
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments([10, 20, 400]),
      throwsFormatException,
    );
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments(
        [...valid]..[0] = double.nan,
      ),
      throwsFormatException,
    );
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments(
        [...valid]..[10] = 'invalid surface',
      ),
      throwsFormatException,
    );
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments(
        [...valid]..[12] = 1.5,
      ),
      throwsFormatException,
    );
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments(
        [...valid]..[16] = 0,
      ),
      throwsFormatException,
    );
    expect(
      () => AvaViewerPointerEvent.fromChannelArguments(
        [...valid]..[14] = double.nan,
      ),
      throwsFormatException,
    );
  });

  test('decodes animation completion with its playback identity', () {
    final event = AvaAnimationFinishedEvent.fromChannelArguments([
      42,
      'AvaReactHeadNormal',
    ]);

    expect(event.playbackId, 42);
    expect(event.animationName, 'AvaReactHeadNormal');
  });

  test('rejects malformed animation completion data', () {
    expect(
      () => AvaAnimationFinishedEvent.fromChannelArguments([
        0,
        'AvaReactHeadNormal',
      ]),
      throwsFormatException,
    );
    expect(
      () => AvaAnimationFinishedEvent.fromChannelArguments([1, '']),
      throwsFormatException,
    );
  });
}
