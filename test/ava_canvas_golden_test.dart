import 'dart:convert';
import 'dart:io';

import 'package:ava/canvas/ava_canvas_engine.dart';
import 'package:ava/models/ava_canvas_protocol.dart';
import 'package:ava/ui/dashboard/ava_canvas_panel.dart';
import 'package:ava/ui/design/ava_design.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('dynamic canvas lower panel matches iPhone layout', (
    tester,
  ) async {
    await _renderPanel(
      tester,
      deviceSize: const Size(390, 844),
      galleryIndex: 0,
    );
    await expectLater(
      find.byType(MaterialApp),
      matchesGoldenFile('goldens/ava_canvas_iphone.png'),
    );
  });

  testWidgets('dynamic canvas lower panel matches iPad layout', (
    tester,
  ) async {
    await _renderPanel(
      tester,
      deviceSize: const Size(1024, 1366),
      galleryIndex: 7,
    );
    await expectLater(
      find.byType(MaterialApp),
      matchesGoldenFile('goldens/ava_canvas_ipad.png'),
    );
  });

  testWidgets('advanced explore canvas is layered and readable on iPhone', (
    tester,
  ) async {
    await _renderExplorePanel(tester, deviceSize: const Size(390, 844));
    await expectLater(
      find.byType(MaterialApp),
      matchesGoldenFile('goldens/ava_canvas_explore_iphone.png'),
    );
  });
}

Future<void> _renderExplorePanel(
  WidgetTester tester, {
  required Size deviceSize,
}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = deviceSize;
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);

  final transaction = (jsonDecode(
    File('protocol/fixtures/ava_canvas_explore_v3.json').readAsStringSync(),
  ) as Map)
      .cast<String, Object?>();
  final engine = AvaCanvasEngine();
  final result = engine.apply(AvaCanvasTransaction.fromJson(transaction));
  expect(result.accepted, isTrue, reason: result.rejection?.toString());
  engine.performInteraction('explore_condensation');

  await tester.pumpWidget(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: buildAvaTheme(),
      home: MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: Scaffold(
          backgroundColor: AvaColors.background,
          body: Align(
            alignment: Alignment.bottomCenter,
            child: SizedBox(
              height: deviceSize.height / 2,
              child: AvaCanvasPanel(
                engine: engine,
                onClose: engine.dismissPanel,
              ),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
  expect(tester.takeException(), isNull);
}

Future<void> _renderPanel(
  WidgetTester tester, {
  required Size deviceSize,
  required int galleryIndex,
}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = deviceSize;
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);

  final decoded = jsonDecode(
    File(
      'protocol/fixtures/ava_canvas_gallery_v3.json',
    ).readAsStringSync(),
  ) as List;
  final entry = decoded[galleryIndex] as Map;
  final transaction = (entry['transaction']! as Map).cast<String, Object?>();
  final engine = AvaCanvasEngine();
  final result = engine.apply(AvaCanvasTransaction.fromJson(transaction));
  expect(result.accepted, isTrue, reason: result.rejection?.toString());

  await tester.pumpWidget(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: buildAvaTheme(),
      home: MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: Scaffold(
          backgroundColor: AvaColors.background,
          body: Align(
            alignment: Alignment.bottomCenter,
            child: SizedBox(
              height: deviceSize.height / 2,
              child: AvaCanvasPanel(
                engine: engine,
                onClose: engine.dismissPanel,
              ),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
  expect(tester.takeException(), isNull);
}
