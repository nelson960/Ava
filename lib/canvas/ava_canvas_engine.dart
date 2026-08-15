import 'dart:collection';
import 'dart:ui';

import 'package:flutter/foundation.dart';

import '../models/ava_canvas_protocol.dart';
import 'ava_canvas_path_parser.dart';

enum AvaCanvasRejectionCode {
  sceneMismatch,
  revisionMismatch,
  duplicateNode,
  unknownNode,
  invalidParent,
  parentCycle,
  hierarchyTooDeep,
  nodeLimit,
  invalidPath,
  invalidBinding,
  invalidState,
  invalidOperation,
}

final class AvaCanvasRejected implements Exception {
  const AvaCanvasRejected({
    required this.code,
    required this.message,
    required this.transactionId,
  });

  final AvaCanvasRejectionCode code;
  final String message;
  final String transactionId;

  @override
  String toString() => 'AvaCanvasRejected(${code.name}): $message';
}

final class AvaCanvasAnimationSpec {
  const AvaCanvasAnimationSpec({
    required this.nodeId,
    required this.property,
    required this.fromValue,
    required this.toValue,
    required this.duration,
    required this.delay,
    required this.easing,
    this.repeat = false,
    this.autoreverse = false,
  });

  final String nodeId;
  final String property;
  final double fromValue;
  final double toValue;
  final Duration duration;
  final Duration delay;
  final String easing;
  final bool repeat;
  final bool autoreverse;
}

final class AvaCanvasScene {
  AvaCanvasScene._({
    required this.id,
    required this.revision,
    required Map<String, AvaCanvasNode> nodes,
    required Map<String, AvaCanvasInteractionBinding> bindings,
    required Map<String, AvaCanvasStateVariable> stateVariables,
    required Map<String, Object> stateValues,
    required this.focusedNodeId,
    required this.selectedNodeId,
    required this.interactionRevision,
  })  : nodes = UnmodifiableMapView(Map.of(nodes)),
        bindings = UnmodifiableMapView(Map.of(bindings)),
        stateVariables = UnmodifiableMapView(Map.of(stateVariables)),
        stateValues = UnmodifiableMapView(Map.of(stateValues));

  final String id;
  final int revision;
  final Map<String, AvaCanvasNode> nodes;
  final Map<String, AvaCanvasInteractionBinding> bindings;
  final Map<String, AvaCanvasStateVariable> stateVariables;
  final Map<String, Object> stateValues;
  final String? focusedNodeId;
  final String? selectedNodeId;
  final int interactionRevision;

  Map<String, Object?> toJson() => {
        'sceneId': id,
        'revision': revision,
        'nodes': [for (final node in nodes.values) node.toJson()],
        'stateVariables': [
          for (final variable in stateVariables.values) variable.toJson(),
        ],
        'stateValues': Map<String, Object>.of(stateValues),
        'bindings': [
          for (final binding in bindings.values) binding.toJson(),
        ],
        'focusedNodeId': focusedNodeId,
        'selectedNodeId': selectedNodeId,
        'interactionRevision': interactionRevision,
      };
}

final class AvaCanvasApplyResult {
  const AvaCanvasApplyResult._({
    required this.accepted,
    this.scene,
    this.animations = const [],
    this.rejection,
  });

  factory AvaCanvasApplyResult.accepted({
    required AvaCanvasScene scene,
    required List<AvaCanvasAnimationSpec> animations,
  }) =>
      AvaCanvasApplyResult._(
        accepted: true,
        scene: scene,
        animations: List.unmodifiable(animations),
      );

  factory AvaCanvasApplyResult.rejected(AvaCanvasRejected rejection) =>
      AvaCanvasApplyResult._(accepted: false, rejection: rejection);

  final bool accepted;
  final AvaCanvasScene? scene;
  final List<AvaCanvasAnimationSpec> animations;
  final AvaCanvasRejected? rejection;
}

final class AvaCanvasInteractionOutcome {
  const AvaCanvasInteractionOutcome({
    required this.sceneId,
    required this.sceneRevision,
    required this.interactionRevision,
    required this.bindingId,
    required this.action,
    this.value,
  });

  final String sceneId;
  final int sceneRevision;
  final int interactionRevision;
  final String bindingId;
  final String action;
  final Object? value;

  Map<String, Object?> toJson() => {
        'sceneId': sceneId,
        'sceneRevision': sceneRevision,
        'interactionRevision': interactionRevision,
        'bindingId': bindingId,
        'action': action,
        'value': value,
      };
}

final class AvaCanvasEngine extends ChangeNotifier {
  AvaCanvasScene? _scene;
  bool _panelVisible = false;
  int _animationGeneration = 0;
  List<AvaCanvasAnimationSpec> _lastAnimations = const [];
  final Map<String, bool> _visibilityOverrides = {};
  final Map<String, Offset> _translationOverrides = {};

  AvaCanvasScene? get scene => _scene;
  bool get panelVisible => _panelVisible && _scene != null;
  int get animationGeneration => _animationGeneration;
  List<AvaCanvasAnimationSpec> get lastAnimations => _lastAnimations;

  AvaCanvasApplyResult apply(AvaCanvasTransaction transaction) {
    final before = _scene;
    try {
      final result = _applyOrThrow(transaction, before);
      _scene = result.scene;
      _panelVisible = true;
      _lastAnimations = result.animations;
      if (_lastAnimations.isNotEmpty) {
        _animationGeneration += 1;
      }
      _removeStaleOverrides(result.scene!);
      notifyListeners();
      return result;
    } on AvaCanvasRejected catch (error) {
      return AvaCanvasApplyResult.rejected(error);
    } on FormatException catch (error) {
      return AvaCanvasApplyResult.rejected(
        AvaCanvasRejected(
          code: AvaCanvasRejectionCode.invalidPath,
          message: error.message.toString(),
          transactionId: transaction.id,
        ),
      );
    }
  }

  void dismissPanel() {
    if (!_panelVisible) return;
    _panelVisible = false;
    notifyListeners();
  }

  void clear() {
    if (_scene == null && !_panelVisible) return;
    _scene = null;
    _panelVisible = false;
    _lastAnimations = const [];
    _visibilityOverrides.clear();
    _translationOverrides.clear();
    notifyListeners();
  }

  Map<String, Object?> effectiveNodeJson(AvaCanvasNode node) {
    final json = node.toJson();
    final visibility = _visibilityOverrides[node.id];
    if (visibility != null) json['visible'] = visibility;
    final translation = _translationOverrides[node.id];
    if (translation != null) {
      final transform = (json['transform']! as Map).cast<String, Object?>();
      transform['translateX'] =
          (transform['translateX']! as num).toDouble() + translation.dx;
      transform['translateY'] =
          (transform['translateY']! as num).toDouble() + translation.dy;
    }
    final current = _scene;
    final selectedNodeId = current?.selectedNodeId;
    if (current != null &&
        selectedNodeId != null &&
        node.id != selectedNodeId &&
        _exploreTargetNodeIds(current).contains(node.id)) {
      final style = (json['style']! as Map).cast<String, Object?>();
      style['opacity'] = (style['opacity']! as num).toDouble() * 0.52;
    }
    return json;
  }

  AvaCanvasInteractionOutcome performInteraction(
    String bindingId, {
    Offset? logicalPosition,
  }) {
    final current = _scene;
    if (current == null) {
      throw StateError('Cannot interact without an active canvas scene.');
    }
    final binding = current.bindings[bindingId];
    if (binding == null) {
      throw StateError('Unknown canvas binding "$bindingId".');
    }
    final action = binding.action;
    final kind = binding.actionKind;
    Object? value;
    switch (kind) {
      case 'explore':
        final target = action['targetNodeId']! as String;
        final revealNodeIds = (action['revealNodeIds']! as List).cast<String>();
        final animateNodeIds =
            (action['animateNodeIds']! as List).cast<String>();
        if (action['exclusive']! as bool) {
          for (final other in current.bindings.values) {
            if (other.actionKind != 'explore' || other.id == binding.id) {
              continue;
            }
            for (final nodeId
                in (other.action['revealNodeIds']! as List).cast<String>()) {
              _visibilityOverrides[nodeId] = false;
            }
          }
        }
        final newlyRevealed = <String>[];
        for (final nodeId in revealNodeIds) {
          final wasVisible =
              _visibilityOverrides[nodeId] ?? current.nodes[nodeId]!.visible;
          _visibilityOverrides[nodeId] = true;
          if (!wasVisible) newlyRevealed.add(nodeId);
        }
        _scene = _copyScene(current, selectedNodeId: target);
        _queueExploreAnimations(
          targetNodeId: target,
          revealNodeIds: newlyRevealed,
          animateNodeIds: animateNodeIds,
        );
        value = {
          'targetNodeId': target,
          'revealedNodeIds': revealNodeIds,
        };
        break;
      case 'toggleVisibility':
        final target = action['targetNodeId']! as String;
        final base = current.nodes[target]!.visible;
        final visible = !(_visibilityOverrides[target] ?? base);
        _visibilityOverrides[target] = visible;
        if (visible) _queueRevealAnimation(target);
        value = visible;
        break;
      case 'reveal':
        final target = action['targetNodeId']! as String;
        final wasVisible =
            _visibilityOverrides[target] ?? current.nodes[target]!.visible;
        _visibilityOverrides[target] = true;
        if (!wasVisible) _queueRevealAnimation(target);
        value = true;
        break;
      case 'select':
        value = action['targetNodeId']! as String;
        _scene = _copyScene(
          current,
          selectedNodeId: value as String,
          interactionRevision: current.interactionRevision + 1,
        );
        notifyListeners();
        return _outcome(bindingId, kind, value);
      case 'dragWithin':
        if (logicalPosition == null) {
          throw ArgumentError.notNull('logicalPosition');
        }
        final bounds = (action['bounds']! as Map).cast<String, Object?>();
        final position = Offset(
          logicalPosition.dx
              .clamp(
                (bounds['left']! as num).toDouble(),
                (bounds['right']! as num).toDouble(),
              )
              .toDouble(),
          logicalPosition.dy
              .clamp(
                (bounds['top']! as num).toDouble(),
                (bounds['bottom']! as num).toDouble(),
              )
              .toDouble(),
        );
        final target = action['targetNodeId']! as String;
        _translationOverrides[target] =
            position - _nodeAnchor(current.nodes[target]!);
        value = {'x': position.dx, 'y': position.dy};
        break;
      case 'toggleState':
        final stateId = action['targetStateId']! as String;
        final currentValue = current.stateValues[stateId]! as bool;
        value = !currentValue;
        _setStateValue(current, stateId, value);
        break;
      case 'setScalar':
      case 'setEnum':
        final stateId = action['targetStateId']! as String;
        value = action['value']!;
        _setStateValue(current, stateId, value);
        break;
      case 'reset':
        _visibilityOverrides.clear();
        _translationOverrides.clear();
        final initialValues = {
          for (final variable in current.stateVariables.values)
            variable.id: variable.initial,
        };
        _scene = AvaCanvasScene._(
          id: current.id,
          revision: current.revision,
          nodes: current.nodes,
          bindings: current.bindings,
          stateVariables: current.stateVariables,
          stateValues: initialValues,
          focusedNodeId: null,
          selectedNodeId: null,
          interactionRevision: current.interactionRevision + 1,
        );
        notifyListeners();
        return _outcome(bindingId, kind, null);
      case 'submitOutcome':
        value = {
          'outcomeId': action['outcomeId'],
          'value': action['value'],
        };
        break;
      default:
        throw StateError('Unsupported validated canvas action "$kind".');
    }

    final latest = _scene ?? current;
    _scene = _copyScene(
      latest,
      interactionRevision: latest.interactionRevision + 1,
    );
    notifyListeners();
    return _outcome(bindingId, kind, value);
  }

  void _queueRevealAnimation(String nodeId) {
    _lastAnimations = [
      AvaCanvasAnimationSpec(
        nodeId: nodeId,
        property: 'opacity',
        fromValue: 0,
        toValue: 1,
        duration: const Duration(milliseconds: 240),
        delay: Duration.zero,
        easing: 'easeOut',
      ),
    ];
    _animationGeneration += 1;
  }

  void _queueExploreAnimations({
    required String targetNodeId,
    required List<String> revealNodeIds,
    required List<String> animateNodeIds,
  }) {
    _lastAnimations = [
      AvaCanvasAnimationSpec(
        nodeId: targetNodeId,
        property: 'scale',
        fromValue: 0.96,
        toValue: 1,
        duration: const Duration(milliseconds: 220),
        delay: Duration.zero,
        easing: 'easeOut',
      ),
      for (final nodeId in revealNodeIds)
        AvaCanvasAnimationSpec(
          nodeId: nodeId,
          property: 'opacity',
          fromValue: 0,
          toValue: 1,
          duration: const Duration(milliseconds: 260),
          delay: const Duration(milliseconds: 70),
          easing: 'easeOut',
        ),
      for (final nodeId in animateNodeIds)
        AvaCanvasAnimationSpec(
          nodeId: nodeId,
          property: 'opacity',
          fromValue: 0.28,
          toValue: 1,
          duration: const Duration(milliseconds: 360),
          delay: const Duration(milliseconds: 40),
          easing: 'easeInOut',
        ),
    ];
    _animationGeneration += 1;
  }

  AvaCanvasApplyResult _applyOrThrow(
    AvaCanvasTransaction transaction,
    AvaCanvasScene? before,
  ) {
    if (before != null && before.id != transaction.sceneId) {
      throw _reject(
        transaction,
        AvaCanvasRejectionCode.sceneMismatch,
        'Active scene ${before.id} cannot accept ${transaction.sceneId}.',
      );
    }
    final expectedRevision = before?.revision ?? 0;
    if (transaction.baseRevision != expectedRevision) {
      throw _reject(
        transaction,
        AvaCanvasRejectionCode.revisionMismatch,
        'Expected baseRevision $expectedRevision, '
        'received ${transaction.baseRevision}.',
      );
    }

    final nodes = LinkedHashMap<String, AvaCanvasNode>.of(
      before?.nodes ?? const {},
    );
    String? focusedNodeId = before?.focusedNodeId;
    String? selectedNodeId = before?.selectedNodeId;
    final animations = <AvaCanvasAnimationSpec>[];
    for (final operation in transaction.operations) {
      switch (operation.kind) {
        case AvaCanvasOperationKind.create:
          final node = operation.node!;
          if (nodes.containsKey(node.id)) {
            throw _reject(
              transaction,
              AvaCanvasRejectionCode.duplicateNode,
              'Canvas node "${node.id}" already exists.',
            );
          }
          nodes[node.id] = node;
          break;
        case AvaCanvasOperationKind.update:
          final node = operation.node!;
          if (!nodes.containsKey(node.id)) {
            throw _unknownNode(transaction, node.id);
          }
          nodes[node.id] = node;
          break;
        case AvaCanvasOperationKind.delete:
          final nodeId = operation.nodeId!;
          if (nodes.remove(nodeId) == null) {
            throw _unknownNode(transaction, nodeId);
          }
          if (focusedNodeId == nodeId) focusedNodeId = null;
          if (selectedNodeId == nodeId) selectedNodeId = null;
          break;
        case AvaCanvasOperationKind.reveal:
          final nodeId = operation.nodeId!;
          final node = nodes[nodeId];
          if (node == null) throw _unknownNode(transaction, nodeId);
          final json = node.toJson()..['visible'] = true;
          nodes[nodeId] = AvaCanvasNode.fromJson(json);
          break;
        case AvaCanvasOperationKind.focus:
          final nodeId = operation.nodeId!;
          if (!nodes.containsKey(nodeId)) {
            throw _unknownNode(transaction, nodeId);
          }
          focusedNodeId = nodeId;
          break;
        case AvaCanvasOperationKind.animate:
          final nodeId = operation.nodeId!;
          if (!nodes.containsKey(nodeId)) {
            throw _unknownNode(transaction, nodeId);
          }
          final json = operation.toJson();
          animations.add(
            AvaCanvasAnimationSpec(
              nodeId: nodeId,
              property: json['property']! as String,
              fromValue: (json['fromValue']! as num).toDouble(),
              toValue: (json['toValue']! as num).toDouble(),
              duration: Duration(milliseconds: json['durationMs']! as int),
              delay: Duration(milliseconds: json['delayMs']! as int),
              easing: json['easing']! as String,
              repeat: json['repeat']! as bool,
              autoreverse: json['autoreverse']! as bool,
            ),
          );
          break;
        case AvaCanvasOperationKind.clear:
          nodes.clear();
          focusedNodeId = null;
          break;
      }
    }

    if (nodes.length > avaCanvasMaxNodesPerTurn) {
      throw _reject(
        transaction,
        AvaCanvasRejectionCode.nodeLimit,
        'Canvas scene exceeds the $avaCanvasMaxNodesPerTurn-node limit.',
      );
    }

    final bindings = {
      for (final binding in transaction.bindings) binding.id: binding,
    };
    final stateVariables = {
      for (final variable in transaction.stateVariables) variable.id: variable,
    };
    final stateValues = <String, Object>{
      for (final variable in stateVariables.values)
        variable.id: _preservedStateValue(before, variable),
    };
    _validateScene(
      transaction,
      nodes,
      bindings,
      stateVariables,
      stateValues,
    );

    final scene = AvaCanvasScene._(
      id: transaction.sceneId,
      revision: transaction.revision,
      nodes: nodes,
      bindings: bindings,
      stateVariables: stateVariables,
      stateValues: stateValues,
      focusedNodeId: focusedNodeId,
      selectedNodeId: selectedNodeId,
      interactionRevision: before?.interactionRevision ?? 0,
    );
    return AvaCanvasApplyResult.accepted(
      scene: scene,
      animations: animations,
    );
  }

  void _validateScene(
    AvaCanvasTransaction transaction,
    Map<String, AvaCanvasNode> nodes,
    Map<String, AvaCanvasInteractionBinding> bindings,
    Map<String, AvaCanvasStateVariable> stateVariables,
    Map<String, Object> stateValues,
  ) {
    for (final node in nodes.values) {
      final parentId = node.parentId;
      if (parentId != null) {
        final parent = nodes[parentId];
        if (parent == null || parent.kind != AvaCanvasNodeKind.group) {
          throw _reject(
            transaction,
            AvaCanvasRejectionCode.invalidParent,
            'Node "${node.id}" requires an existing group parent.',
          );
        }
      }
      _validateHierarchy(transaction, node.id, nodes);
      if (node.kind == AvaCanvasNodeKind.path) {
        try {
          AvaCanvasPathParser.parse(node.toJson()['pathData']! as String);
        } on FormatException catch (error) {
          throw _reject(
            transaction,
            AvaCanvasRejectionCode.invalidPath,
            'Node "${node.id}" has invalid path data: ${error.message}',
          );
        }
      }
      if (node.kind == AvaCanvasNodeKind.text) {
        final text = node.toJson()['text']! as String;
        if ('\n'.allMatches(text).length > 3) {
          throw _reject(
            transaction,
            AvaCanvasRejectionCode.invalidOperation,
            'Text node "${node.id}" exceeds four lines.',
          );
        }
      }
    }

    for (final binding in bindings.values) {
      if (!nodes.containsKey(binding.nodeId)) {
        throw _invalidBinding(
          transaction,
          binding,
          'source node "${binding.nodeId}" does not exist',
        );
      }
      final action = binding.action;
      switch (binding.actionKind) {
        case 'explore':
          final target = action['targetNodeId']! as String;
          final revealNodeIds =
              (action['revealNodeIds']! as List).cast<String>();
          final animateNodeIds =
              (action['animateNodeIds']! as List).cast<String>();
          final referenced = {target, ...revealNodeIds, ...animateNodeIds};
          final missing = referenced.difference(nodes.keys.toSet());
          if (missing.isNotEmpty) {
            throw _invalidBinding(
              transaction,
              binding,
              'explore references unknown nodes $missing',
            );
          }
          if (revealNodeIds.any((nodeId) => nodes[nodeId]!.visible)) {
            throw _invalidBinding(
              transaction,
              binding,
              'explore reveal nodes must begin hidden',
            );
          }
          break;
        case 'toggleVisibility':
        case 'select':
        case 'reveal':
          final target = action['targetNodeId']! as String;
          if (!nodes.containsKey(target)) {
            throw _invalidBinding(
              transaction,
              binding,
              'target node "$target" does not exist',
            );
          }
          break;
        case 'dragWithin':
          final target = action['targetNodeId']! as String;
          final node = nodes[target];
          if (node == null || node.kind == AvaCanvasNodeKind.group) {
            throw _invalidBinding(
              transaction,
              binding,
              'drag target "$target" must be a drawable node',
            );
          }
          break;
        case 'toggleState':
          final stateId = action['targetStateId']! as String;
          final variable = stateVariables[stateId];
          if (variable == null ||
              variable.type != AvaCanvasStateVariableType.boolean) {
            throw _invalidState(transaction, binding, stateId);
          }
          break;
        case 'setScalar':
          final stateId = action['targetStateId']! as String;
          final variable = stateVariables[stateId];
          if (variable == null ||
              variable.type != AvaCanvasStateVariableType.scalar) {
            throw _invalidState(transaction, binding, stateId);
          }
          _validateBindingStateValue(
            transaction,
            binding,
            variable,
            action['value']!,
          );
          break;
        case 'setEnum':
          final stateId = action['targetStateId']! as String;
          final variable = stateVariables[stateId];
          if (variable == null ||
              variable.type != AvaCanvasStateVariableType.enumValue) {
            throw _invalidState(transaction, binding, stateId);
          }
          _validateBindingStateValue(
            transaction,
            binding,
            variable,
            action['value']!,
          );
          break;
        case 'reset':
        case 'submitOutcome':
          break;
        default:
          throw _invalidBinding(
            transaction,
            binding,
            'unsupported action "${binding.actionKind}"',
          );
      }
    }

    for (final entry in stateValues.entries) {
      final variable = stateVariables[entry.key]!;
      try {
        _validateStateValue(variable, entry.value);
      } on StateError catch (error) {
        throw _reject(
          transaction,
          AvaCanvasRejectionCode.invalidState,
          error.message,
        );
      }
    }
  }

  void _validateHierarchy(
    AvaCanvasTransaction transaction,
    String nodeId,
    Map<String, AvaCanvasNode> nodes,
  ) {
    final visited = <String>{nodeId};
    var current = nodes[nodeId];
    var depth = 0;
    while (current?.parentId != null) {
      final parentId = current!.parentId!;
      if (!visited.add(parentId)) {
        throw _reject(
          transaction,
          AvaCanvasRejectionCode.parentCycle,
          'Canvas parent cycle includes "$nodeId".',
        );
      }
      depth += 1;
      if (depth > 8) {
        throw _reject(
          transaction,
          AvaCanvasRejectionCode.hierarchyTooDeep,
          'Canvas hierarchy exceeds eight levels.',
        );
      }
      current = nodes[parentId];
    }
  }

  void _validateBindingStateValue(
    AvaCanvasTransaction transaction,
    AvaCanvasInteractionBinding binding,
    AvaCanvasStateVariable variable,
    Object value,
  ) {
    try {
      _validateStateValue(variable, value);
    } on StateError catch (error) {
      throw _invalidBinding(
        transaction,
        binding,
        error.message,
      );
    }
  }

  Object _preservedStateValue(
    AvaCanvasScene? before,
    AvaCanvasStateVariable variable,
  ) {
    final previousVariable = before?.stateVariables[variable.id];
    final previousValue = before?.stateValues[variable.id];
    if (previousVariable != null &&
        previousVariable.type == variable.type &&
        previousValue != null) {
      try {
        _validateStateValue(variable, previousValue);
        return previousValue;
      } on StateError {
        // A changed declaration intentionally resets to its explicit initial.
      }
    }
    return variable.initial;
  }

  void _setStateValue(
    AvaCanvasScene current,
    String stateId,
    Object value,
  ) {
    final variable = current.stateVariables[stateId];
    if (variable == null) {
      throw StateError('Unknown canvas state "$stateId".');
    }
    _validateStateValue(variable, value);
    final values = Map<String, Object>.of(current.stateValues)
      ..[stateId] = value;
    _scene = AvaCanvasScene._(
      id: current.id,
      revision: current.revision,
      nodes: current.nodes,
      bindings: current.bindings,
      stateVariables: current.stateVariables,
      stateValues: values,
      focusedNodeId: current.focusedNodeId,
      selectedNodeId: current.selectedNodeId,
      interactionRevision: current.interactionRevision,
    );
  }

  Offset _nodeAnchor(AvaCanvasNode node) {
    final json = node.toJson();
    Offset point(Object? raw) {
      final value = (raw! as Map).cast<String, Object?>();
      return Offset(
        (value['x']! as num).toDouble(),
        (value['y']! as num).toDouble(),
      );
    }

    switch (node.kind) {
      case AvaCanvasNodeKind.group:
        throw StateError('A group cannot be a drag target.');
      case AvaCanvasNodeKind.text:
        return Offset(
          (json['x']! as num).toDouble(),
          (json['y']! as num).toDouble(),
        );
      case AvaCanvasNodeKind.line:
      case AvaCanvasNodeKind.arrow:
        final start = point(json['start']);
        final end = point(json['end']);
        return Offset(
          (start.dx + end.dx) / 2,
          (start.dy + end.dy) / 2,
        );
      case AvaCanvasNodeKind.circle:
      case AvaCanvasNodeKind.ellipse:
        return point(json['center']);
      case AvaCanvasNodeKind.rect:
        return Offset(
          (json['x']! as num).toDouble() +
              (json['width']! as num).toDouble() / 2,
          (json['y']! as num).toDouble() +
              (json['height']! as num).toDouble() / 2,
        );
      case AvaCanvasNodeKind.polygon:
      case AvaCanvasNodeKind.polyline:
        final points = (json['points']! as List).map(point).toList();
        var minimumX = points.first.dx;
        var maximumX = points.first.dx;
        var minimumY = points.first.dy;
        var maximumY = points.first.dy;
        for (final value in points.skip(1)) {
          if (value.dx < minimumX) minimumX = value.dx;
          if (value.dx > maximumX) maximumX = value.dx;
          if (value.dy < minimumY) minimumY = value.dy;
          if (value.dy > maximumY) maximumY = value.dy;
        }
        return Offset(
          (minimumX + maximumX) / 2,
          (minimumY + maximumY) / 2,
        );
      case AvaCanvasNodeKind.path:
        return AvaCanvasPathParser.parse(
          json['pathData']! as String,
        ).getBounds().center;
    }
  }

  void _validateStateValue(
    AvaCanvasStateVariable variable,
    Object value,
  ) {
    final json = variable.toJson();
    switch (variable.type) {
      case AvaCanvasStateVariableType.boolean:
        if (value is! bool) {
          throw StateError('State "${variable.id}" requires a boolean.');
        }
        break;
      case AvaCanvasStateVariableType.scalar:
        if (value is! num || !value.isFinite) {
          throw StateError('State "${variable.id}" requires a finite scalar.');
        }
        final minimum = (json['minimum']! as num).toDouble();
        final maximum = (json['maximum']! as num).toDouble();
        final step = (json['step']! as num).toDouble();
        final scalar = value.toDouble();
        final steps = (scalar - minimum) / step;
        if (scalar < minimum ||
            scalar > maximum ||
            (steps - steps.round()).abs() > 1e-8) {
          throw StateError(
            'State "${variable.id}" is outside its declared scalar range.',
          );
        }
        break;
      case AvaCanvasStateVariableType.enumValue:
        final values = (json['values']! as List).cast<String>();
        if (value is! String || !values.contains(value)) {
          throw StateError(
            'State "${variable.id}" is outside its declared enum values.',
          );
        }
        break;
    }
  }

  void _removeStaleOverrides(AvaCanvasScene scene) {
    _visibilityOverrides.removeWhere((id, _) => !scene.nodes.containsKey(id));
    _translationOverrides.removeWhere((id, _) => !scene.nodes.containsKey(id));
  }

  AvaCanvasInteractionOutcome _outcome(
    String bindingId,
    String action,
    Object? value,
  ) {
    final current = _scene!;
    return AvaCanvasInteractionOutcome(
      sceneId: current.id,
      sceneRevision: current.revision,
      interactionRevision: current.interactionRevision,
      bindingId: bindingId,
      action: action,
      value: value,
    );
  }

  Set<String> _exploreTargetNodeIds(AvaCanvasScene scene) => {
        for (final binding in scene.bindings.values)
          if (binding.actionKind == 'explore')
            binding.action['targetNodeId']! as String,
      };

  AvaCanvasScene _copyScene(
    AvaCanvasScene source, {
    String? selectedNodeId,
    int? interactionRevision,
  }) =>
      AvaCanvasScene._(
        id: source.id,
        revision: source.revision,
        nodes: source.nodes,
        bindings: source.bindings,
        stateVariables: source.stateVariables,
        stateValues: source.stateValues,
        focusedNodeId: source.focusedNodeId,
        selectedNodeId: selectedNodeId ?? source.selectedNodeId,
        interactionRevision: interactionRevision ?? source.interactionRevision,
      );

  AvaCanvasRejected _unknownNode(
    AvaCanvasTransaction transaction,
    String nodeId,
  ) =>
      _reject(
        transaction,
        AvaCanvasRejectionCode.unknownNode,
        'Canvas node "$nodeId" does not exist.',
      );

  AvaCanvasRejected _invalidBinding(
    AvaCanvasTransaction transaction,
    AvaCanvasInteractionBinding binding,
    String detail,
  ) =>
      _reject(
        transaction,
        AvaCanvasRejectionCode.invalidBinding,
        'Binding "${binding.id}" is invalid: $detail.',
      );

  AvaCanvasRejected _invalidState(
    AvaCanvasTransaction transaction,
    AvaCanvasInteractionBinding binding,
    String stateId,
  ) =>
      _reject(
        transaction,
        AvaCanvasRejectionCode.invalidState,
        'Binding "${binding.id}" targets incompatible state "$stateId".',
      );

  AvaCanvasRejected _reject(
    AvaCanvasTransaction transaction,
    AvaCanvasRejectionCode code,
    String message,
  ) =>
      AvaCanvasRejected(
        code: code,
        message: message,
        transactionId: transaction.id,
      );
}
