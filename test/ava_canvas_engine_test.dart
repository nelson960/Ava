import 'dart:convert';
import 'dart:io';

import 'package:ava/canvas/ava_canvas_engine.dart';
import 'package:ava/canvas/ava_canvas_geometry.dart';
import 'package:ava/canvas/ava_canvas_path_parser.dart';
import 'package:ava/models/ava_canvas_protocol.dart';
import 'package:ava/ui/dashboard/ava_canvas_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('shared renderer capability manifest matches this Flutter build', () {
    final decoded = jsonDecode(
      File(
        'protocol/fixtures/ava_canvas_capabilities_v3.json',
      ).readAsStringSync(),
    );
    final payload = (decoded as Map).cast<String, Object?>();
    final capabilities = AvaCanvasRendererCapabilities.fromJson(payload);
    expect(capabilities.toJson(), payload);
  });

  test('shared gallery uses one general scene grammar', () {
    final gallery = _gallery();
    expect(
      gallery.map((entry) => entry['name']),
      [
        'free_form_plant_cell',
        'solar_relationship',
        'triangle_construction',
        'force_arrows',
        'event_timeline',
        'bar_line_comparison',
        'custom_curved_path',
        'interactive_drag_toggle_reveal',
      ],
    );
    final kinds = <AvaCanvasNodeKind>{};
    for (final entry in gallery) {
      final engine = AvaCanvasEngine();
      final result = engine.apply(_transaction(entry));
      expect(result.accepted, isTrue, reason: result.rejection?.toString());
      expect(engine.scene?.revision, 1);
      expect(engine.panelVisible, isTrue);
      final geometry = AvaCanvasGeometry.build(engine);
      expect(geometry.nodes, isNotEmpty);
      kinds.addAll(engine.scene!.nodes.values.map((node) => node.kind));
      if (entry['name'] == 'solar_relationship') {
        expect(engine.lastAnimations, hasLength(1));
        expect(engine.lastAnimations.single.nodeId, 'solar_earth');
      }
    }
    expect(kinds, AvaCanvasNodeKind.values.toSet());
  });

  test('bounded path parser accepts full grammar and rejects malformed data',
      () {
    final path = AvaCanvasPathParser.parse(
      'M 10 20 L 30 40 H 50 V 60 '
      'C 70 80 90 100 110 120 S 130 140 150 160 '
      'Q 170 180 190 200 T 210 220 A 20 30 0 0 1 250 260 Z',
    );
    expect(path.getBounds().isFinite, isTrue);

    for (final invalid in [
      'L 10 20',
      'M 10',
      'M 0 0 A 10 10 0 2 0 20 20',
      'M 0 0 C 1 2 3',
      'M 0,,0',
    ]) {
      expect(
        () => AvaCanvasPathParser.parse(invalid),
        throwsFormatException,
        reason: invalid,
      );
    }
  });

  test('stale and invalid transactions are rejected atomically', () {
    final engine = AvaCanvasEngine();
    final source = _gallery().first;
    expect(engine.apply(_transaction(source)).accepted, isTrue);
    final before = engine.scene!;
    final beforeJson = before.toJson();

    final invalidJson = _deepCopy(
      source['transaction']! as Map<String, Object?>,
    )
      ..['id'] = 'canvas.gallery.plant.invalid'
      ..['baseRevision'] = 1
      ..['revision'] = 2
      ..['operations'] = [
        {'operation': 'delete', 'nodeId': 'missing_node'},
      ];
    final invalid = AvaCanvasTransaction.fromJson(invalidJson);
    final rejected = engine.apply(invalid);
    expect(rejected.accepted, isFalse);
    expect(
      rejected.rejection?.code,
      AvaCanvasRejectionCode.unknownNode,
    );
    expect(engine.scene!.toJson(), beforeJson);

    invalidJson
      ..['baseRevision'] = 2
      ..['revision'] = 3;
    final stale = engine.apply(AvaCanvasTransaction.fromJson(invalidJson));
    expect(
      stale.rejection?.code,
      AvaCanvasRejectionCode.revisionMismatch,
    );
    expect(engine.scene!.toJson(), beforeJson);
  });

  test('node explosion is rejected without corrupting the 96-node scene', () {
    final engine = AvaCanvasEngine();
    final first = _bulkTransaction(
      id: 'canvas.bulk.1',
      baseRevision: 0,
      start: 0,
      count: 64,
    );
    final second = _bulkTransaction(
      id: 'canvas.bulk.2',
      baseRevision: 1,
      start: 64,
      count: 32,
    );
    final overflow = _bulkTransaction(
      id: 'canvas.bulk.3',
      baseRevision: 2,
      start: 96,
      count: 1,
    );
    expect(engine.apply(first).accepted, isTrue);
    expect(engine.apply(second).accepted, isTrue);
    final rejected = engine.apply(overflow);
    expect(rejected.rejection?.code, AvaCanvasRejectionCode.nodeLimit);
    expect(engine.scene?.nodes, hasLength(96));
    expect(engine.scene?.revision, 2);
  });

  test('a repeated validated scene reopens a dismissed canvas panel', () {
    final engine = AvaCanvasEngine();
    final source = _gallery().first;
    final original = _transaction(source);
    expect(engine.apply(original).accepted, isTrue);
    engine.dismissPanel();
    expect(engine.panelVisible, isFalse);

    final repeatedJson = _deepCopy(original.toJson())
      ..['id'] = 'canvas.gallery.plant.reopen'
      ..['baseRevision'] = 1
      ..['revision'] = 2
      ..['operations'] = [
        {
          'operation': 'update',
          'node': original.operations.first.node!.toJson(),
        },
      ];
    final repeated = AvaCanvasTransaction.fromJson(repeatedJson);
    expect(engine.apply(repeated).accepted, isTrue);
    expect(engine.panelVisible, isTrue);
    expect(engine.scene?.revision, 2);
  });

  test('hit testing and local interactions remain deterministic', () {
    final engine = AvaCanvasEngine();
    final interactive = _gallery().last;
    expect(engine.apply(_transaction(interactive)).accepted, isTrue);

    var geometry = AvaCanvasGeometry.build(engine);
    final drag = geometry.hitTest(
      const Offset(300, 305),
      event: AvaCanvasInteractionEvent.drag,
    );
    expect(drag?.id, 'interactive_drag_knob');
    final dragOutcome = engine.performInteraction(
      drag!.id,
      logicalPosition: const Offset(700, 305),
    );
    expect(dragOutcome.value, {'x': 700.0, 'y': 305.0});
    final knob = engine.effectiveNodeJson(
      engine.scene!.nodes['interactive_knob']!,
    );
    final knobTransform = (knob['transform']! as Map).cast<String, Object?>();
    expect(knobTransform['translateX'], 400.0);
    expect(knobTransform['translateY'], 0.0);

    geometry = AvaCanvasGeometry.build(engine);
    final reveal = geometry.hitTest(
      const Offset(500, 465),
      event: AvaCanvasInteractionEvent.tap,
    );
    expect(reveal?.id, 'interactive_show_answer');
    final animationGeneration = engine.animationGeneration;
    engine.performInteraction(reveal!.id);
    final answer = engine.effectiveNodeJson(
      engine.scene!.nodes['interactive_answer']!,
    );
    expect(answer['visible'], isTrue);
    expect(engine.animationGeneration, animationGeneration + 1);
    expect(engine.lastAnimations, hasLength(1));
    expect(engine.lastAnimations.single.nodeId, 'interactive_answer');
    expect(engine.lastAnimations.single.property, 'opacity');

    engine.performInteraction('interactive_toggle_seen');
    engine.performInteraction('interactive_set_value');
    engine.performInteraction('interactive_set_detail');
    expect(engine.scene!.stateValues, {
      'answer_seen': true,
      'marker_value': 75.0,
      'detail_level': 'expanded',
    });

    engine.performInteraction('interactive_reset_all');
    expect(engine.scene!.stateValues, {
      'answer_seen': false,
      'marker_value': 25.0,
      'detail_level': 'simple',
    });
    expect(
      engine.effectiveNodeJson(
        engine.scene!.nodes['interactive_answer']!,
      )['visible'],
      isFalse,
    );
  });

  test('compound explore interactions are exclusive, staged, and resettable',
      () {
    final engine = AvaCanvasEngine();
    final payload = (jsonDecode(
      File('protocol/fixtures/ava_canvas_explore_v3.json').readAsStringSync(),
    ) as Map)
        .cast<String, Object?>();
    final applied = engine.apply(AvaCanvasTransaction.fromJson(payload));
    expect(applied.accepted, isTrue, reason: applied.rejection?.toString());
    expect(
      engine.lastAnimations.map((animation) => animation.delay.inMilliseconds),
      [0, 60, 120, 210, 300, 390, 470],
    );

    final firstGeneration = engine.animationGeneration;
    final evaporation = engine.performInteraction('explore_evaporation');
    expect(evaporation.action, 'explore');
    expect(engine.scene!.selectedNodeId, 'water_ocean');
    expect(
      engine.effectiveNodeJson(
        engine.scene!.nodes['water_note_card_evap']!,
      )['visible'],
      isTrue,
    );
    expect(engine.lastAnimations, hasLength(4));
    expect(engine.animationGeneration, firstGeneration + 1);
    final dimmedCloudStyle = (engine.effectiveNodeJson(
      engine.scene!.nodes['water_cloud']!,
    )['style']! as Map)
        .cast<String, Object?>();
    expect(dimmedCloudStyle['opacity'], closeTo(0.52, 0.001));

    engine.performInteraction('explore_condensation');
    expect(engine.scene!.selectedNodeId, 'water_cloud');
    expect(
      engine.effectiveNodeJson(
        engine.scene!.nodes['water_note_card_evap']!,
      )['visible'],
      isFalse,
    );
    expect(
      engine.effectiveNodeJson(
        engine.scene!.nodes['water_note_text_cloud']!,
      )['visible'],
      isTrue,
    );
    final selectedCloudStyle = (engine.effectiveNodeJson(
      engine.scene!.nodes['water_cloud']!,
    )['style']! as Map)
        .cast<String, Object?>();
    expect(selectedCloudStyle['opacity'], 1.0);
    final geometry = AvaCanvasGeometry.build(engine);
    final cardBounds = geometry.byId['water_note_card_cloud']!.logicalBounds;
    final textBounds = geometry.byId['water_note_text_cloud']!.logicalBounds;
    expect(
      cardBounds.contains(textBounds.topLeft),
      isTrue,
      reason: 'card=$cardBounds text=$textBounds',
    );
    expect(
      cardBounds.contains(textBounds.bottomRight),
      isTrue,
      reason: 'card=$cardBounds text=$textBounds',
    );

    engine.performInteraction('reset_water_cycle');
    expect(engine.scene!.selectedNodeId, isNull);
    expect(
      engine.effectiveNodeJson(
        engine.scene!.nodes['water_note_text_cloud']!,
      )['visible'],
      isFalse,
    );
    final restoredOceanStyle = (engine.effectiveNodeJson(
      engine.scene!.nodes['water_ocean']!,
    )['style']! as Map)
        .cast<String, Object?>();
    expect(restoredOceanStyle['opacity'], 1.0);
  });

  testWidgets('advanced scene hit targets explore repeatedly from the canvas',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(1000, 600);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);

    final engine = AvaCanvasEngine();
    final payload = (jsonDecode(
      File('protocol/fixtures/ava_canvas_explore_v3.json').readAsStringSync(),
    ) as Map)
        .cast<String, Object?>();
    expect(
        engine.apply(AvaCanvasTransaction.fromJson(payload)).accepted, isTrue);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AvaCanvasView(engine: engine),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tapAt(const Offset(245, 450));
    await tester.pump();
    expect(engine.scene!.selectedNodeId, 'water_ocean');

    await tester.tapAt(const Offset(570, 230));
    await tester.pump();
    expect(engine.scene!.selectedNodeId, 'water_cloud');

    await tester.tapAt(const Offset(775, 425));
    await tester.pump();
    expect(engine.scene!.selectedNodeId, 'water_raindrop');
  });

  test('scene preparation remains inside a display-frame budget', () {
    final engine = AvaCanvasEngine();
    expect(engine.apply(_transaction(_gallery().last)).accepted, isTrue);
    const iterations = 240;
    final stopwatch = Stopwatch()..start();
    for (var index = 0; index < iterations; index += 1) {
      final geometry = AvaCanvasGeometry.build(engine);
      expect(geometry.nodes, isNotEmpty);
    }
    stopwatch.stop();
    final averageMicroseconds = stopwatch.elapsedMicroseconds / iterations;
    expect(
      averageMicroseconds,
      lessThan(const Duration(milliseconds: 16).inMicroseconds),
      reason: 'Scene preparation averaged '
          '${averageMicroseconds.toStringAsFixed(1)} µs.',
    );
  });

  test('renderer preserves authored labels and expands compact hit targets',
      () {
    final engine = AvaCanvasEngine();
    final transaction = AvaCanvasTransaction.fromJson({
      'id': 'canvas.layout.1',
      'sceneId': 'canvas.layout',
      'baseRevision': 0,
      'revision': 1,
      'operations': [
        {
          'operation': 'create',
          'node': _textNode(
            id: 'evaporation_label',
            x: 500,
            y: 250,
            text: 'Evaporation',
          ),
        },
        {
          'operation': 'create',
          'node': _textNode(
            id: 'condensation_label',
            x: 510,
            y: 255,
            text: 'Condensation',
          ),
        },
        {
          'operation': 'create',
          'node': {
            'id': 'tiny_control',
            'parentId': null,
            'label': 'Select the marker',
            'visible': true,
            'transform': _identityTransform(),
            'style': {
              'fill': 'accent',
              'stroke': 'ink',
              'strokeWidth': 2.0,
              'opacity': 1.0,
              'strokeCap': 'round',
            },
            'kind': 'circle',
            'center': {'x': 500.0, 'y': 400.0},
            'radius': 10.0,
          },
        },
      ],
      'stateVariables': <Object?>[],
      'bindings': [
        {
          'id': 'select_tiny_control',
          'nodeId': 'tiny_control',
          'event': 'tap',
          'action': {'kind': 'select', 'targetNodeId': 'tiny_control'},
        },
      ],
    });

    final applied = engine.apply(transaction);
    expect(applied.accepted, isTrue, reason: applied.rejection?.toString());
    expect(engine.panelVisible, isTrue);
    final geometry = AvaCanvasGeometry.build(engine);
    final first = geometry.byId['evaporation_label']!.logicalBounds;
    final second = geometry.byId['condensation_label']!.logicalBounds;
    expect(first.overlaps(second), isTrue);
    expect(
        (Offset.zero & avaCanvasLogicalSize).contains(first.topLeft), isTrue);
    expect(
      (Offset.zero & avaCanvasLogicalSize).contains(second.bottomRight),
      isTrue,
    );

    final selected = geometry.hitTest(
      const Offset(520, 400),
      event: AvaCanvasInteractionEvent.tap,
    );
    expect(selected?.id, 'select_tiny_control');
  });

  testWidgets(
      'authored gradients, shadows, pivots, repeating motion, and physical hit targets render safely',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 300);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    final engine = AvaCanvasEngine();
    final transaction = AvaCanvasTransaction.fromJson({
      'id': 'canvas.authored.reference.1',
      'sceneId': 'canvas.authored.reference',
      'baseRevision': 0,
      'revision': 1,
      'operations': [
        {
          'operation': 'create',
          'node': {
            'id': 'orbit_group',
            'parentId': null,
            'label': 'Orbiting planet group',
            'visible': true,
            'transform': {
              ..._identityTransform(),
              'pivotX': 500.0,
              'pivotY': 300.0,
            },
            'style': {
              'fill': 'transparent',
              'stroke': 'transparent',
              'strokeWidth': 0.0,
              'opacity': 1.0,
              'strokeCap': 'round',
            },
            'kind': 'group',
          },
        },
        {
          'operation': 'create',
          'node': {
            'id': 'space_region',
            'parentId': null,
            'label': 'Deep blue space region',
            'visible': true,
            'transform': _identityTransform(),
            'style': {
              'fill': 'ink',
              'stroke': 'transparent',
              'strokeWidth': 0.0,
              'opacity': 1.0,
              'strokeCap': 'round',
              'gradient': {
                'start': {'x': 100.0, 'y': 100.0},
                'end': {'x': 900.0, 'y': 500.0},
                'stops': [
                  {'offset': 0.0, 'color': '#102A43'},
                  {'offset': 1.0, 'color': '#243B53'},
                ],
              },
              'shadow': {
                'color': '#102A43',
                'opacity': 0.22,
                'blurRadius': 12.0,
                'offsetX': 0.0,
                'offsetY': 6.0,
              },
            },
            'kind': 'ellipse',
            'center': {'x': 500.0, 'y': 300.0},
            'radiusX': 420.0,
            'radiusY': 220.0,
          },
        },
        {
          'operation': 'create',
          'node': {
            'id': 'small_planet',
            'parentId': 'orbit_group',
            'label': 'Small interactive planet',
            'visible': true,
            'transform': _identityTransform(),
            'style': {
              'fill': 'accent',
              'stroke': 'paper',
              'strokeWidth': 2.0,
              'opacity': 1.0,
              'strokeCap': 'round',
              'strokeColor': '#F7FAF8',
              'radialGradient': {
                'center': {'x': 704.0, 'y': 294.0},
                'radius': 18.0,
                'stops': [
                  {'offset': 0.0, 'color': '#BEEBFF'},
                  {'offset': 0.48, 'color': '#39A9DB'},
                  {'offset': 1.0, 'color': '#155E75'},
                ],
              },
              'shadow': {
                'color': '#102A43',
                'opacity': 0.28,
                'blurRadius': 8.0,
                'offsetX': 0.0,
                'offsetY': 3.0,
              },
            },
            'kind': 'circle',
            'center': {'x': 710.0, 'y': 300.0},
            'radius': 10.0,
          },
        },
        {
          'operation': 'animate',
          'nodeId': 'orbit_group',
          'property': 'rotationDegrees',
          'fromValue': 0.0,
          'toValue': 360.0,
          'durationMs': 12000,
          'delayMs': 0,
          'easing': 'linear',
          'repeat': true,
          'autoreverse': false,
        },
      ],
      'stateVariables': <Object?>[],
      'bindings': [
        {
          'id': 'select_small_planet',
          'nodeId': 'small_planet',
          'event': 'tap',
          'action': {'kind': 'select', 'targetNodeId': 'small_planet'},
        },
      ],
    });
    expect(engine.apply(transaction).accepted, isTrue);
    AvaCanvasInteractionOutcome? outcome;
    await tester.pumpWidget(
      MaterialApp(
        home: AvaCanvasView(
          engine: engine,
          onOutcome: (value) => outcome = value,
        ),
      ),
    );
    await tester.pump(const Duration(milliseconds: 400));
    expect(tester.takeException(), isNull);

    // The visible planet is only 20 logical units. At this viewport scale the
    // tap is outside that shape but inside the required 44-point hit target.
    await tester.tapAt(const Offset(292, 150));
    await tester.pump();
    expect(outcome?.bindingId, 'select_small_planet');
  });

  for (final size in const [Size(390, 300), Size(1024, 600)]) {
    testWidgets('all dynamic scenes render at ${size.width}x${size.height}', (
      tester,
    ) async {
      for (final entry in _gallery()) {
        final engine = AvaCanvasEngine();
        final applied = engine.apply(_transaction(entry));
        expect(applied.accepted, isTrue);
        await tester.pumpWidget(
          MaterialApp(
            home: MediaQuery(
              data: const MediaQueryData(disableAnimations: true),
              child: Center(
                child: SizedBox.fromSize(
                  size: size,
                  child: AvaCanvasView(engine: engine),
                ),
              ),
            ),
          ),
        );
        await tester.pump();
        expect(tester.takeException(), isNull, reason: entry['name'] as String);
        expect(
            find.byKey(const ValueKey('ava_dynamic_canvas')), findsOneWidget);
      }
    });
  }
}

List<Map<String, Object?>> _gallery() {
  final decoded = jsonDecode(
    File(
      'protocol/fixtures/ava_canvas_gallery_v3.json',
    ).readAsStringSync(),
  );
  return [
    for (final entry in decoded as List) (entry as Map).cast<String, Object?>(),
  ];
}

AvaCanvasTransaction _transaction(Map<String, Object?> galleryEntry) =>
    AvaCanvasTransaction.fromJson(
      (galleryEntry['transaction']! as Map).cast<String, Object?>(),
    );

Map<String, Object?> _deepCopy(Map<String, Object?> source) =>
    (jsonDecode(jsonEncode(source)) as Map).cast<String, Object?>();

AvaCanvasTransaction _bulkTransaction({
  required String id,
  required int baseRevision,
  required int start,
  required int count,
}) {
  return AvaCanvasTransaction.fromJson({
    'id': id,
    'sceneId': 'canvas.bulk',
    'baseRevision': baseRevision,
    'revision': baseRevision + 1,
    'operations': [
      for (var index = start; index < start + count; index += 1)
        {
          'operation': 'create',
          'node': _rectNode('node_$index', index.toDouble()),
        },
    ],
    'stateVariables': <Object?>[],
    'bindings': <Object?>[],
  });
}

Map<String, Object?> _rectNode(String id, double offset) => {
      'id': id,
      'parentId': null,
      'label': 'Node $id',
      'visible': true,
      'transform': {
        'translateX': 0.0,
        'translateY': 0.0,
        'scale': 1.0,
        'rotationDegrees': 0.0,
      },
      'style': {
        'fill': 'accentSoft',
        'stroke': 'accent',
        'strokeWidth': 1.0,
        'opacity': 1.0,
        'strokeCap': 'round',
      },
      'kind': 'rect',
      'x': offset,
      'y': 20.0,
      'width': 5.0,
      'height': 5.0,
      'cornerRadius': 0.0,
    };

Map<String, Object?> _textNode({
  required String id,
  required double x,
  required double y,
  required String text,
}) =>
    {
      'id': id,
      'parentId': null,
      'label': text,
      'visible': true,
      'transform': _identityTransform(),
      'style': {
        'fill': 'ink',
        'stroke': 'transparent',
        'strokeWidth': 0.0,
        'opacity': 1.0,
        'strokeCap': 'round',
      },
      'kind': 'text',
      'x': x,
      'y': y,
      'text': text,
      'fontSize': 29.0,
      'fontWeight': 'bold',
      'textAnchor': 'middle',
    };

Map<String, Object?> _identityTransform() => {
      'translateX': 0.0,
      'translateY': 0.0,
      'scale': 1.0,
      'rotationDegrees': 0.0,
    };
