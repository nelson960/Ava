import 'dart:convert';

const avaCanvasMaxNodesPerTurn = 96;
const avaCanvasMaxOperationsPerTransaction = 64;
const avaCanvasMaxBindingsPerTransaction = 24;
const avaCanvasMaxStateVariables = 16;
const avaCanvasMaxPathCharacters = 2000;
const avaCanvasMaxPoints = 64;
const avaCanvasMaxTextCharacters = 240;
const avaCanvasMaxTransactionsPerTurn = 8;
const avaCanvasLogicalWidth = 1000.0;
const avaCanvasLogicalHeight = 600.0;

enum AvaCanvasNodeKind {
  group,
  text,
  line,
  arrow,
  circle,
  ellipse,
  rect,
  polygon,
  polyline,
  path,
}

enum AvaCanvasOperationKind {
  create,
  update,
  delete,
  reveal,
  focus,
  animate,
  clear,
}

enum AvaCanvasInteractionEvent {
  tap,
  drag,
}

final class AvaCanvasRendererCapabilities {
  const AvaCanvasRendererCapabilities._();

  static const current = AvaCanvasRendererCapabilities._();

  static const nodeKinds = [
    'group',
    'text',
    'line',
    'arrow',
    'circle',
    'ellipse',
    'rect',
    'polygon',
    'polyline',
    'path',
  ];
  static const operations = [
    'create',
    'update',
    'delete',
    'reveal',
    'focus',
    'animate',
    'clear',
  ];
  static const interactions = [
    'explore',
    'toggleVisibility',
    'select',
    'reveal',
    'dragWithin',
    'toggleState',
    'setScalar',
    'setEnum',
    'reset',
    'submitOutcome',
  ];
  static const animationProperties = [
    'opacity',
    'translateX',
    'translateY',
    'rotationDegrees',
    'scale',
  ];
  static const colorTokens = [
    'ink',
    'muted',
    'accent',
    'accentSoft',
    'positive',
    'warning',
    'paper',
    'transparent',
  ];

  factory AvaCanvasRendererCapabilities.fromJson(
    Map<String, Object?> json,
  ) {
    _requireExactKeys(
      json,
      const {
        'grammarVersion',
        'logicalWidth',
        'logicalHeight',
        'nodeKinds',
        'operations',
        'interactions',
        'animationProperties',
        'colorTokens',
        'limits',
      },
      'canvas renderer capabilities',
    );
    final limits = _requiredObject(
      json['limits'],
      'canvas renderer capabilities.limits',
    );
    _requireExactKeys(
      limits,
      const {
        'maxNodes',
        'maxOperationsPerTransaction',
        'maxBindingsPerTransaction',
        'maxStateVariables',
        'maxPathCharacters',
        'maxPathCommands',
        'maxPathNumbers',
        'maxPointsPerNode',
        'maxTextCharacters',
        'maxTextLines',
        'maxHierarchyDepth',
      },
      'canvas renderer capabilities.limits',
    );
    final parsed = {
      'grammarVersion': _requiredInt(
        json,
        'grammarVersion',
        'canvas renderer capabilities',
      ),
      'logicalWidth': _finiteNumber(
        json,
        'logicalWidth',
        'canvas renderer capabilities',
      ),
      'logicalHeight': _finiteNumber(
        json,
        'logicalHeight',
        'canvas renderer capabilities',
      ),
      'nodeKinds': _stringList(json['nodeKinds'], 'nodeKinds'),
      'operations': _stringList(json['operations'], 'operations'),
      'interactions': _stringList(json['interactions'], 'interactions'),
      'animationProperties': _stringList(
        json['animationProperties'],
        'animationProperties',
      ),
      'colorTokens': _stringList(json['colorTokens'], 'colorTokens'),
      'limits': {
        for (final key in limits.keys)
          key: _requiredInt(
            limits,
            key,
            'canvas renderer capabilities.limits',
          ),
      },
    };
    if (!_jsonEquivalent(parsed, current.toJson())) {
      throw const FormatException(
        'canvas renderer capability manifest does not match this build.',
      );
    }
    return current;
  }

  Map<String, Object?> toJson() => {
        'grammarVersion': 3,
        'logicalWidth': avaCanvasLogicalWidth,
        'logicalHeight': avaCanvasLogicalHeight,
        'nodeKinds': [...nodeKinds],
        'operations': [...operations],
        'interactions': [...interactions],
        'animationProperties': [...animationProperties],
        'colorTokens': [...colorTokens],
        'limits': {
          'maxNodes': avaCanvasMaxNodesPerTurn,
          'maxOperationsPerTransaction': avaCanvasMaxOperationsPerTransaction,
          'maxBindingsPerTransaction': avaCanvasMaxBindingsPerTransaction,
          'maxStateVariables': avaCanvasMaxStateVariables,
          'maxPathCharacters': avaCanvasMaxPathCharacters,
          'maxPathCommands': 128,
          'maxPathNumbers': 512,
          'maxPointsPerNode': avaCanvasMaxPoints,
          'maxTextCharacters': avaCanvasMaxTextCharacters,
          'maxTextLines': 4,
          'maxHierarchyDepth': 8,
        },
      };
}

final class AvaCanvasNode {
  AvaCanvasNode._(this._json, this.id, this.kind);

  static final RegExp _pathCharacters =
      RegExp(r'^[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]+$');

  final Map<String, Object?> _json;
  final String id;
  final AvaCanvasNodeKind kind;

  String? get parentId => _json['parentId'] as String?;
  String get label => _json['label']! as String;
  bool get visible => _json['visible']! as bool;
  Map<String, Object?> get transform =>
      _requiredObject(_json['transform'], 'canvas node.transform');
  Map<String, Object?> get style =>
      _requiredObject(_json['style'], 'canvas node.style');

  factory AvaCanvasNode.fromJson(Map<String, Object?> json) {
    final kind = _enumByName(
      AvaCanvasNodeKind.values,
      _requiredString(json, 'kind', 'canvas node'),
      'canvas node.kind',
    );
    const common = {
      'id',
      'parentId',
      'label',
      'visible',
      'transform',
      'style',
      'kind',
    };
    final specific = switch (kind) {
      AvaCanvasNodeKind.group => const <String>{},
      AvaCanvasNodeKind.text => const {
          'x',
          'y',
          'text',
          'fontSize',
          'fontWeight',
          'textAnchor',
        },
      AvaCanvasNodeKind.line || AvaCanvasNodeKind.arrow => const {
          'start',
          'end',
        },
      AvaCanvasNodeKind.circle => const {'center', 'radius'},
      AvaCanvasNodeKind.ellipse => const {
          'center',
          'radiusX',
          'radiusY',
        },
      AvaCanvasNodeKind.rect => const {
          'x',
          'y',
          'width',
          'height',
          'cornerRadius',
        },
      AvaCanvasNodeKind.polygon || AvaCanvasNodeKind.polyline => const {
          'points',
        },
      AvaCanvasNodeKind.path => const {'pathData'},
    };
    _requireExactKeys(json, {...common, ...specific}, 'canvas node');

    final id = _nodeId(json, 'id', 'canvas node');
    final parent = json['parentId'];
    if (parent != null) {
      _validateNodeId(parent, 'canvas node.parentId');
      if (parent == id) {
        throw const FormatException(
          'canvas node cannot use itself as parentId.',
        );
      }
    }
    _boundedString(json, 'label', 'canvas node', 120);
    _requiredBool(json, 'visible', 'canvas node');
    _validateTransform(
      _requiredObject(json['transform'], 'canvas node.transform'),
    );
    _validateStyle(_requiredObject(json['style'], 'canvas node.style'));

    switch (kind) {
      case AvaCanvasNodeKind.group:
        break;
      case AvaCanvasNodeKind.text:
        _coordinate(json, 'x', 'canvas text', -2000, 3000);
        _coordinate(json, 'y', 'canvas text', -1200, 1800);
        _boundedString(
          json,
          'text',
          'canvas text',
          avaCanvasMaxTextCharacters,
        );
        _coordinate(json, 'fontSize', 'canvas text', 8, 96);
        _allowedString(
          json,
          'fontWeight',
          'canvas text',
          const {'regular', 'medium', 'bold'},
        );
        _allowedString(
          json,
          'textAnchor',
          'canvas text',
          const {'start', 'middle', 'end'},
        );
        final fill =
            (_requiredObject(json['style'], 'canvas text.style')['fill']!
                as String);
        if (!const {'ink', 'muted', 'positive'}.contains(fill)) {
          throw const FormatException(
            'canvas text fill must use a high-contrast foreground token.',
          );
        }
        break;
      case AvaCanvasNodeKind.line || AvaCanvasNodeKind.arrow:
        _validatePoint(_requiredObject(json['start'], 'canvas line.start'));
        _validatePoint(_requiredObject(json['end'], 'canvas line.end'));
        break;
      case AvaCanvasNodeKind.circle:
        _validatePoint(_requiredObject(json['center'], 'canvas circle.center'));
        _coordinate(json, 'radius', 'canvas circle', 0.000001, 2000);
        break;
      case AvaCanvasNodeKind.ellipse:
        _validatePoint(
          _requiredObject(json['center'], 'canvas ellipse.center'),
        );
        _coordinate(json, 'radiusX', 'canvas ellipse', 0.000001, 2000);
        _coordinate(json, 'radiusY', 'canvas ellipse', 0.000001, 1200);
        break;
      case AvaCanvasNodeKind.rect:
        _coordinate(json, 'x', 'canvas rect', -2000, 3000);
        _coordinate(json, 'y', 'canvas rect', -1200, 1800);
        _coordinate(json, 'width', 'canvas rect', 0.000001, 3000);
        _coordinate(json, 'height', 'canvas rect', 0.000001, 1800);
        _coordinate(json, 'cornerRadius', 'canvas rect', 0, 300);
        break;
      case AvaCanvasNodeKind.polygon || AvaCanvasNodeKind.polyline:
        final points = json['points'];
        final minimum = kind == AvaCanvasNodeKind.polygon ? 3 : 2;
        if (points is! List ||
            points.length < minimum ||
            points.length > avaCanvasMaxPoints) {
          throw FormatException(
            '${kind.name} requires $minimum-$avaCanvasMaxPoints points.',
          );
        }
        for (var index = 0; index < points.length; index += 1) {
          _validatePoint(
            _requiredObject(points[index], '${kind.name}.points[$index]'),
          );
        }
        break;
      case AvaCanvasNodeKind.path:
        final path = _boundedString(
          json,
          'pathData',
          'canvas path',
          avaCanvasMaxPathCharacters,
        );
        if (!_pathCharacters.hasMatch(path)) {
          throw const FormatException(
            'canvas path.pathData contains unsupported characters.',
          );
        }
        final commandCount =
            path.runes.where((rune) => _isAsciiLetter(rune)).length;
        if (commandCount < 1 || commandCount > 128) {
          throw const FormatException(
            'canvas path requires between 1 and 128 commands.',
          );
        }
        break;
    }
    return AvaCanvasNode._(_deepCopyMap(json), id, kind);
  }

  Map<String, Object?> toJson() => _deepCopyMap(_json);
}

List<String> _stringList(Object? raw, String context) {
  if (raw is! List || raw.isEmpty) {
    throw FormatException('canvas renderer capabilities.$context is invalid.');
  }
  final result = <String>[];
  for (var index = 0; index < raw.length; index += 1) {
    final value = raw[index];
    if (value is! String || value.isEmpty) {
      throw FormatException(
        'canvas renderer capabilities.$context[$index] is invalid.',
      );
    }
    result.add(value);
  }
  return result;
}

bool _jsonEquivalent(Object? left, Object? right) {
  if (left is num && right is num) {
    return left.toDouble() == right.toDouble();
  }
  if (left is List && right is List) {
    if (left.length != right.length) return false;
    for (var index = 0; index < left.length; index += 1) {
      if (!_jsonEquivalent(left[index], right[index])) return false;
    }
    return true;
  }
  if (left is Map && right is Map) {
    if (left.length != right.length) return false;
    for (final key in left.keys) {
      if (!right.containsKey(key) || !_jsonEquivalent(left[key], right[key])) {
        return false;
      }
    }
    return true;
  }
  return left == right;
}

final class AvaCanvasOperation {
  AvaCanvasOperation._(
    this._json,
    this.kind, {
    this.node,
    this.nodeId,
  });

  final Map<String, Object?> _json;
  final AvaCanvasOperationKind kind;
  final AvaCanvasNode? node;
  final String? nodeId;

  String? get createdNodeId =>
      kind == AvaCanvasOperationKind.create ? node?.id : null;

  factory AvaCanvasOperation.fromJson(Map<String, Object?> json) {
    final kind = _enumByName(
      AvaCanvasOperationKind.values,
      _requiredString(json, 'operation', 'canvas operation'),
      'canvas operation.operation',
    );
    return switch (kind) {
      AvaCanvasOperationKind.create ||
      AvaCanvasOperationKind.update =>
        _nodeOperation(json, kind),
      AvaCanvasOperationKind.delete ||
      AvaCanvasOperationKind.reveal ||
      AvaCanvasOperationKind.focus =>
        _nodeIdOperation(json, kind),
      AvaCanvasOperationKind.animate => _animationOperation(json),
      AvaCanvasOperationKind.clear => _clearOperation(json),
    };
  }

  static AvaCanvasOperation _nodeOperation(
    Map<String, Object?> json,
    AvaCanvasOperationKind kind,
  ) {
    _requireExactKeys(json, const {'operation', 'node'}, 'canvas operation');
    final node = AvaCanvasNode.fromJson(
      _requiredObject(json['node'], 'canvas operation.node'),
    );
    return AvaCanvasOperation._(
      _deepCopyMap(json),
      kind,
      node: node,
      nodeId: node.id,
    );
  }

  static AvaCanvasOperation _nodeIdOperation(
    Map<String, Object?> json,
    AvaCanvasOperationKind kind,
  ) {
    _requireExactKeys(
      json,
      const {'operation', 'nodeId'},
      'canvas operation',
    );
    final nodeId = _nodeId(json, 'nodeId', 'canvas operation');
    return AvaCanvasOperation._(
      _deepCopyMap(json),
      kind,
      nodeId: nodeId,
    );
  }

  static AvaCanvasOperation _animationOperation(
    Map<String, Object?> json,
  ) {
    _requireRequiredAndAllowedKeys(
      json,
      const {
        'operation',
        'nodeId',
        'property',
        'fromValue',
        'toValue',
        'durationMs',
        'delayMs',
        'easing',
      },
      const {
        'operation',
        'nodeId',
        'property',
        'fromValue',
        'toValue',
        'durationMs',
        'delayMs',
        'easing',
        'repeat',
        'autoreverse',
      },
      'canvas animation',
    );
    final nodeId = _nodeId(json, 'nodeId', 'canvas animation');
    final property = _allowedString(
      json,
      'property',
      'canvas animation',
      const {
        'opacity',
        'translateX',
        'translateY',
        'rotationDegrees',
        'scale',
      },
    );
    final range = switch (property) {
      'opacity' => (0.0, 1.0),
      'translateX' => (-2000.0, 2000.0),
      'translateY' => (-1200.0, 1200.0),
      'rotationDegrees' => (-360.0, 360.0),
      'scale' => (0.05, 8.0),
      _ => throw StateError('Validated unsupported animation $property.'),
    };
    _coordinate(
      json,
      'fromValue',
      'canvas animation',
      range.$1,
      range.$2,
    );
    _coordinate(
      json,
      'toValue',
      'canvas animation',
      range.$1,
      range.$2,
    );
    final duration = _requiredInt(json, 'durationMs', 'canvas animation');
    if (duration < 80 || duration > 20000) {
      throw const FormatException(
        'canvas animation.durationMs must be between 80 and 20000.',
      );
    }
    final delay = _requiredInt(json, 'delayMs', 'canvas animation');
    if (delay < 0 || delay > 2500) {
      throw const FormatException(
        'canvas animation.delayMs must be between 0 and 2500.',
      );
    }
    _allowedString(
      json,
      'easing',
      'canvas animation',
      const {'linear', 'easeInOut', 'easeOut'},
    );
    final repeat = json['repeat'] ?? false;
    final autoreverse = json['autoreverse'] ?? false;
    if (repeat is! bool || autoreverse is! bool) {
      throw const FormatException(
        'canvas animation repeat and autoreverse must be booleans.',
      );
    }
    if (autoreverse && !repeat) {
      throw const FormatException(
          'canvas animation autoreverse requires repeat.');
    }
    if (repeat && duration < 600) {
      throw const FormatException(
        'repeating canvas animations must last at least 600ms.',
      );
    }
    final normalized = _deepCopyMap(json)
      ..putIfAbsent('repeat', () => false)
      ..putIfAbsent('autoreverse', () => false);
    return AvaCanvasOperation._(
      normalized,
      AvaCanvasOperationKind.animate,
      nodeId: nodeId,
    );
  }

  static AvaCanvasOperation _clearOperation(Map<String, Object?> json) {
    _requireExactKeys(json, const {'operation'}, 'canvas clear operation');
    return AvaCanvasOperation._(
      _deepCopyMap(json),
      AvaCanvasOperationKind.clear,
    );
  }

  Map<String, Object?> toJson() => _deepCopyMap(_json);
}

final class AvaCanvasInteractionBinding {
  AvaCanvasInteractionBinding._(
    this._json, {
    required this.id,
    required this.nodeId,
    required this.event,
  });

  final Map<String, Object?> _json;
  final String id;
  final String nodeId;
  final AvaCanvasInteractionEvent event;

  Map<String, Object?> get action =>
      _requiredObject(_json['action'], 'canvas binding.action');
  String get actionKind => action['kind']! as String;

  factory AvaCanvasInteractionBinding.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {'id', 'nodeId', 'event', 'action'},
      'canvas binding',
    );
    final id = _nodeId(json, 'id', 'canvas binding');
    final nodeId = _nodeId(json, 'nodeId', 'canvas binding');
    final event = _enumByName(
      AvaCanvasInteractionEvent.values,
      _requiredString(json, 'event', 'canvas binding'),
      'canvas binding.event',
    );
    final action = _requiredObject(json['action'], 'canvas binding.action');
    final actionKind = _requiredString(action, 'kind', 'canvas binding.action');
    switch (actionKind) {
      case 'explore':
        _requireExactKeys(
          action,
          const {
            'kind',
            'targetNodeId',
            'revealNodeIds',
            'animateNodeIds',
            'exclusive',
          },
          'canvas binding.action',
        );
        final targetNodeId = _nodeId(
          action,
          'targetNodeId',
          'canvas binding.action',
        );
        final revealNodeIds = _nodeIdList(
          action['revealNodeIds'],
          'canvas binding.action.revealNodeIds',
          minimum: 1,
          maximum: 4,
        );
        _nodeIdList(
          action['animateNodeIds'],
          'canvas binding.action.animateNodeIds',
          minimum: 0,
          maximum: 4,
        );
        if (revealNodeIds.contains(targetNodeId)) {
          throw const FormatException(
            'canvas explore target must remain visible.',
          );
        }
        if (action['exclusive'] is! bool) {
          throw const FormatException(
            'canvas binding.action.exclusive must be a boolean.',
          );
        }
        break;
      case 'toggleVisibility':
      case 'select':
      case 'reveal':
        _requireExactKeys(
          action,
          const {'kind', 'targetNodeId'},
          'canvas binding.action',
        );
        _nodeId(action, 'targetNodeId', 'canvas binding.action');
        break;
      case 'toggleState':
        _requireExactKeys(
          action,
          const {'kind', 'targetStateId'},
          'canvas binding.action',
        );
        _nodeId(action, 'targetStateId', 'canvas binding.action');
        break;
      case 'setScalar':
        _requireExactKeys(
          action,
          const {'kind', 'targetStateId', 'value'},
          'canvas binding.action',
        );
        _nodeId(action, 'targetStateId', 'canvas binding.action');
        final value = action['value'];
        if (value is! num || !value.isFinite) {
          throw const FormatException(
            'canvas binding.action.value must be finite.',
          );
        }
        break;
      case 'setEnum':
        _requireExactKeys(
          action,
          const {'kind', 'targetStateId', 'value'},
          'canvas binding.action',
        );
        _nodeId(action, 'targetStateId', 'canvas binding.action');
        _boundedString(action, 'value', 'canvas binding.action', 40);
        break;
      case 'dragWithin':
        _requireExactKeys(
          action,
          const {'kind', 'targetNodeId', 'bounds'},
          'canvas binding.action',
        );
        _nodeId(action, 'targetNodeId', 'canvas binding.action');
        _validateBounds(
          _requiredObject(action['bounds'], 'canvas binding.action.bounds'),
        );
        break;
      case 'reset':
        _requireExactKeys(action, const {'kind'}, 'canvas binding.action');
        break;
      case 'submitOutcome':
        _requireExactKeys(
          action,
          const {'kind', 'outcomeId', 'value'},
          'canvas binding.action',
        );
        _nodeId(action, 'outcomeId', 'canvas binding.action');
        _boundedString(action, 'value', 'canvas binding.action', 80);
        break;
      default:
        throw FormatException(
          'canvas binding.action.kind has unsupported value "$actionKind".',
        );
    }
    final usesDrag = actionKind == 'dragWithin';
    if ((event == AvaCanvasInteractionEvent.drag) != usesDrag) {
      throw const FormatException(
        'drag events and dragWithin actions must occur together.',
      );
    }
    return AvaCanvasInteractionBinding._(
      _deepCopyMap(json),
      id: id,
      nodeId: nodeId,
      event: event,
    );
  }

  Map<String, Object?> toJson() => _deepCopyMap(_json);
}

List<String> _nodeIdList(
  Object? raw,
  String context, {
  required int minimum,
  required int maximum,
}) {
  if (raw is! List || raw.length < minimum || raw.length > maximum) {
    throw FormatException(
      '$context must contain $minimum-$maximum node IDs.',
    );
  }
  final result = <String>[];
  for (var index = 0; index < raw.length; index += 1) {
    final value = raw[index];
    if (value is! String || value.isEmpty || value.length > 80) {
      throw FormatException('$context[$index] is not a valid node ID.');
    }
    result.add(value);
  }
  if (result.toSet().length != result.length) {
    throw FormatException('$context must not contain duplicates.');
  }
  return result;
}

enum AvaCanvasStateVariableType {
  boolean,
  scalar,
  enumValue,
}

final class AvaCanvasStateVariable {
  AvaCanvasStateVariable._({
    required this.id,
    required this.type,
    required Map<String, Object?> json,
  }) : _json = _deepCopyMap(json);

  final String id;
  final AvaCanvasStateVariableType type;
  final Map<String, Object?> _json;

  Object get initial => _json['initial']!;

  factory AvaCanvasStateVariable.fromJson(Map<String, Object?> json) {
    final id = _nodeId(json, 'id', 'canvas state variable');
    final typeName = _requiredString(json, 'type', 'canvas state variable');
    final type = switch (typeName) {
      'boolean' => AvaCanvasStateVariableType.boolean,
      'scalar' => AvaCanvasStateVariableType.scalar,
      'enum' => AvaCanvasStateVariableType.enumValue,
      _ => throw FormatException(
          'canvas state variable.type has unsupported value "$typeName".',
        ),
    };
    switch (type) {
      case AvaCanvasStateVariableType.boolean:
        _requireExactKeys(
          json,
          const {'id', 'type', 'initial'},
          'boolean canvas state',
        );
        _requiredBool(json, 'initial', 'boolean canvas state');
        break;
      case AvaCanvasStateVariableType.scalar:
        _requireExactKeys(
          json,
          const {
            'id',
            'type',
            'minimum',
            'maximum',
            'step',
            'initial',
          },
          'scalar canvas state',
        );
        final minimum = _finiteNumber(json, 'minimum', 'scalar canvas state');
        final maximum = _finiteNumber(json, 'maximum', 'scalar canvas state');
        final step = _finiteNumber(json, 'step', 'scalar canvas state');
        final initial = _finiteNumber(json, 'initial', 'scalar canvas state');
        if (minimum >= maximum ||
            step <= 0 ||
            step > maximum - minimum ||
            initial < minimum ||
            initial > maximum) {
          throw const FormatException(
            'scalar canvas state range is incoherent.',
          );
        }
        final steps = (initial - minimum) / step;
        if ((steps - steps.round()).abs() > 1e-8) {
          throw const FormatException(
            'scalar canvas state initial must align to its step.',
          );
        }
        break;
      case AvaCanvasStateVariableType.enumValue:
        _requireExactKeys(
          json,
          const {'id', 'type', 'values', 'initial'},
          'enum canvas state',
        );
        final values = json['values'];
        if (values is! List || values.length < 2 || values.length > 16) {
          throw const FormatException(
            'enum canvas state requires 2-16 values.',
          );
        }
        final parsed = <String>[];
        for (var index = 0; index < values.length; index += 1) {
          final value = values[index];
          if (value is! String ||
              value.trim().isEmpty ||
              value.runes.length > 40) {
            throw FormatException(
              'enum canvas state.values[$index] is invalid.',
            );
          }
          parsed.add(value);
        }
        if (parsed.toSet().length != parsed.length) {
          throw const FormatException(
            'enum canvas state values must be unique.',
          );
        }
        final initial =
            _boundedString(json, 'initial', 'enum canvas state', 40);
        if (!parsed.contains(initial)) {
          throw const FormatException(
            'enum canvas state initial must be one of its values.',
          );
        }
        break;
    }
    return AvaCanvasStateVariable._(id: id, type: type, json: json);
  }

  Map<String, Object?> toJson() => _deepCopyMap(_json);
}

final class AvaCanvasTransaction {
  AvaCanvasTransaction._({
    required this.id,
    required this.sceneId,
    required this.baseRevision,
    required this.revision,
    required this.operations,
    required this.stateVariables,
    required this.bindings,
  });

  final String id;
  final String sceneId;
  final int baseRevision;
  final int revision;
  final List<AvaCanvasOperation> operations;
  final List<AvaCanvasStateVariable> stateVariables;
  final List<AvaCanvasInteractionBinding> bindings;

  factory AvaCanvasTransaction.fromJson(Map<String, Object?> json) {
    _requireExactKeys(
      json,
      const {
        'id',
        'sceneId',
        'baseRevision',
        'revision',
        'operations',
        'stateVariables',
        'bindings',
      },
      'canvas transaction',
    );
    final id = _protocolId(json, 'id', 'canvas transaction');
    final sceneId = _protocolId(json, 'sceneId', 'canvas transaction');
    final baseRevision =
        _requiredInt(json, 'baseRevision', 'canvas transaction');
    final revision = _requiredInt(json, 'revision', 'canvas transaction');
    if (baseRevision < 0 ||
        baseRevision > 10000 ||
        revision != baseRevision + 1) {
      throw const FormatException(
        'canvas transaction revision must equal baseRevision + 1.',
      );
    }

    final rawOperations = json['operations'];
    if (rawOperations is! List ||
        rawOperations.isEmpty ||
        rawOperations.length > avaCanvasMaxOperationsPerTransaction) {
      throw const FormatException(
        'canvas transaction requires 1-64 operations.',
      );
    }
    final operations = [
      for (var index = 0; index < rawOperations.length; index += 1)
        AvaCanvasOperation.fromJson(
          _requiredObject(
            rawOperations[index],
            'canvas transaction.operations[$index]',
          ),
        ),
    ];
    final created = <String>{};
    for (final operation in operations) {
      final nodeId = operation.createdNodeId;
      if (nodeId != null && !created.add(nodeId)) {
        throw FormatException(
          'canvas create operation duplicates node id "$nodeId".',
        );
      }
    }
    final clearCount = operations
        .where((operation) => operation.kind == AvaCanvasOperationKind.clear)
        .length;
    if (clearCount > 0 && (clearCount != 1 || operations.length != 1)) {
      throw const FormatException(
        'clear must be the only operation in a canvas transaction.',
      );
    }

    final rawBindings = json['bindings'];
    if (rawBindings is! List ||
        rawBindings.length > avaCanvasMaxBindingsPerTransaction) {
      throw const FormatException(
        'canvas transaction bindings exceed the limit.',
      );
    }
    final bindings = [
      for (var index = 0; index < rawBindings.length; index += 1)
        AvaCanvasInteractionBinding.fromJson(
          _requiredObject(
            rawBindings[index],
            'canvas transaction.bindings[$index]',
          ),
        ),
    ];
    final bindingIds = <String>{};
    for (final binding in bindings) {
      if (!bindingIds.add(binding.id)) {
        throw FormatException(
          'canvas binding id "${binding.id}" is duplicated.',
        );
      }
    }

    final rawState = json['stateVariables'];
    if (rawState is! List || rawState.length > avaCanvasMaxStateVariables) {
      throw const FormatException(
        'canvas transaction state variables exceed the limit.',
      );
    }
    final stateVariables = [
      for (var index = 0; index < rawState.length; index += 1)
        AvaCanvasStateVariable.fromJson(
          _requiredObject(
            rawState[index],
            'canvas transaction.stateVariables[$index]',
          ),
        ),
    ];
    final stateIds = <String>{};
    for (final variable in stateVariables) {
      if (!stateIds.add(variable.id)) {
        throw FormatException(
          'canvas state id "${variable.id}" is duplicated.',
        );
      }
    }
    return AvaCanvasTransaction._(
      id: id,
      sceneId: sceneId,
      baseRevision: baseRevision,
      revision: revision,
      operations: List.unmodifiable(operations),
      stateVariables: List.unmodifiable(stateVariables),
      bindings: List.unmodifiable(bindings),
    );
  }

  Map<String, Object?> toJson() => {
        'id': id,
        'sceneId': sceneId,
        'baseRevision': baseRevision,
        'revision': revision,
        'operations': [
          for (final operation in operations) operation.toJson(),
        ],
        'stateVariables': [
          for (final variable in stateVariables) variable.toJson(),
        ],
        'bindings': [for (final binding in bindings) binding.toJson()],
      };
}

double _finiteNumber(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = json[key];
  if (value is! num || !value.isFinite) {
    throw FormatException('$context.$key must be a finite number.');
  }
  return value.toDouble();
}

void _validateTransform(Map<String, Object?> json) {
  _requireRequiredAndAllowedKeys(
    json,
    const {'translateX', 'translateY', 'scale', 'rotationDegrees'},
    const {
      'translateX',
      'translateY',
      'scale',
      'rotationDegrees',
      'pivotX',
      'pivotY',
    },
    'canvas transform',
  );
  _coordinate(json, 'translateX', 'canvas transform', -2000, 2000);
  _coordinate(json, 'translateY', 'canvas transform', -1200, 1200);
  _coordinate(json, 'scale', 'canvas transform', 0.05, 8);
  _coordinate(json, 'rotationDegrees', 'canvas transform', -360, 360);
  if (json.containsKey('pivotX')) {
    _coordinate(json, 'pivotX', 'canvas transform', -2000, 3000);
  }
  if (json.containsKey('pivotY')) {
    _coordinate(json, 'pivotY', 'canvas transform', -1200, 1800);
  }
}

void _validateStyle(Map<String, Object?> json) {
  _requireRequiredAndAllowedKeys(
    json,
    const {
      'fill',
      'stroke',
      'strokeWidth',
      'opacity',
      'strokeCap',
    },
    const {
      'fill',
      'stroke',
      'strokeWidth',
      'opacity',
      'strokeCap',
      'fillColor',
      'strokeColor',
      'gradient',
      'radialGradient',
      'shadow',
    },
    'canvas style',
  );
  const colors = {
    'ink',
    'muted',
    'accent',
    'accentSoft',
    'positive',
    'warning',
    'paper',
    'transparent',
  };
  _allowedString(json, 'fill', 'canvas style', colors);
  _allowedString(json, 'stroke', 'canvas style', colors);
  _coordinate(json, 'strokeWidth', 'canvas style', 0, 16);
  _coordinate(json, 'opacity', 'canvas style', 0, 1);
  _allowedString(
    json,
    'strokeCap',
    'canvas style',
    const {'butt', 'round', 'square'},
  );
  _optionalHexColor(json, 'fillColor', 'canvas style');
  _optionalHexColor(json, 'strokeColor', 'canvas style');
  final gradient = json['gradient'];
  final radialGradient = json['radialGradient'];
  if (gradient != null) {
    _validateGradient(_requiredObject(gradient, 'canvas style.gradient'));
  }
  if (radialGradient != null) {
    _validateRadialGradient(
      _requiredObject(radialGradient, 'canvas style.radialGradient'),
    );
  }
  if ([json['fillColor'], gradient, radialGradient]
          .where((value) => value != null)
          .length >
      1) {
    throw const FormatException(
      'canvas style must choose one of fillColor, gradient, or radialGradient.',
    );
  }
  final shadow = json['shadow'];
  if (shadow != null) {
    _validateShadow(_requiredObject(shadow, 'canvas style.shadow'));
  }
}

void _validateRadialGradient(Map<String, Object?> json) {
  _requireExactKeys(
    json,
    const {'center', 'radius', 'stops'},
    'canvas radial gradient',
  );
  _validatePoint(
    _requiredObject(json['center'], 'canvas radial gradient.center'),
  );
  _coordinate(json, 'radius', 'canvas radial gradient', 0.000001, 2000);
  _validateGradientStops(json['stops'], 'canvas radial gradient');
}

void _validateGradient(Map<String, Object?> json) {
  _requireExactKeys(json, const {'start', 'end', 'stops'}, 'canvas gradient');
  _validatePoint(_requiredObject(json['start'], 'canvas gradient.start'));
  _validatePoint(_requiredObject(json['end'], 'canvas gradient.end'));
  _validateGradientStops(json['stops'], 'canvas gradient');
}

void _validateGradientStops(Object? raw, String context) {
  final rawStops = raw;
  if (rawStops is! List || rawStops.length < 2 || rawStops.length > 4) {
    throw FormatException('$context requires 2-4 stops.');
  }
  var previous = -1.0;
  for (var index = 0; index < rawStops.length; index += 1) {
    final stop = _requiredObject(rawStops[index], '$context stop');
    _requireExactKeys(stop, const {'offset', 'color'}, '$context stop');
    final offset = _coordinate(stop, 'offset', '$context stop', 0, 1);
    if (offset <= previous) {
      throw FormatException(
        '$context stops must be strictly ordered.',
      );
    }
    _hexColor(stop['color'], '$context stop.color');
    previous = offset;
  }
  final first = _requiredObject(rawStops.first, '$context stop');
  final last = _requiredObject(rawStops.last, '$context stop');
  if ((first['offset']! as num).toDouble() != 0 ||
      (last['offset']! as num).toDouble() != 1) {
    throw FormatException(
      '$context stops must begin at 0 and end at 1.',
    );
  }
}

void _validateShadow(Map<String, Object?> json) {
  _requireExactKeys(
    json,
    const {'color', 'opacity', 'blurRadius', 'offsetX', 'offsetY'},
    'canvas shadow',
  );
  _hexColor(json['color'], 'canvas shadow.color');
  _coordinate(json, 'opacity', 'canvas shadow', 0, 0.5);
  _coordinate(json, 'blurRadius', 'canvas shadow', 0, 32);
  _coordinate(json, 'offsetX', 'canvas shadow', -24, 24);
  _coordinate(json, 'offsetY', 'canvas shadow', -24, 24);
}

void _optionalHexColor(
  Map<String, Object?> json,
  String key,
  String context,
) {
  final value = json[key];
  if (value != null) _hexColor(value, '$context.$key');
}

void _hexColor(Object? value, String context) {
  if (value is! String || !RegExp(r'^#[0-9A-Fa-f]{6}$').hasMatch(value)) {
    throw FormatException('$context must be a six-digit hex color.');
  }
}

void _validatePoint(Map<String, Object?> json) {
  _requireExactKeys(json, const {'x', 'y'}, 'canvas point');
  _coordinate(json, 'x', 'canvas point', -2000, 3000);
  _coordinate(json, 'y', 'canvas point', -1200, 1800);
}

void _validateBounds(Map<String, Object?> json) {
  _requireExactKeys(
    json,
    const {'left', 'top', 'right', 'bottom'},
    'canvas bounds',
  );
  final left = _coordinate(json, 'left', 'canvas bounds', -2000, 3000);
  final top = _coordinate(json, 'top', 'canvas bounds', -1200, 1800);
  final right = _coordinate(json, 'right', 'canvas bounds', -2000, 3000);
  final bottom = _coordinate(json, 'bottom', 'canvas bounds', -1200, 1800);
  if (left >= right || top >= bottom) {
    throw const FormatException(
      'canvas bounds must have positive width and height.',
    );
  }
}

double _coordinate(
  Map<String, Object?> json,
  String key,
  String context,
  double minimum,
  double maximum,
) {
  final value = json[key];
  if (value is! num || !value.isFinite) {
    throw FormatException('$context.$key must be a finite number.');
  }
  final result = value.toDouble();
  if (result < minimum || result > maximum) {
    throw FormatException(
      '$context.$key must be between $minimum and $maximum.',
    );
  }
  return result;
}

String _allowedString(
  Map<String, Object?> json,
  String key,
  String context,
  Set<String> allowed,
) {
  final value = _requiredString(json, key, context);
  if (!allowed.contains(value)) {
    throw FormatException('$context.$key has unsupported value "$value".');
  }
  return value;
}

String _boundedString(
  Map<String, Object?> json,
  String key,
  String context,
  int maximum,
) {
  final value = _requiredString(json, key, context);
  if (value.runes.length > maximum) {
    throw FormatException(
        '$context.$key exceeds the $maximum-character limit.');
  }
  return value;
}

String _nodeId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  _validateNodeId(value, '$context.$key');
  return value;
}

void _validateNodeId(Object? value, String context) {
  if (value is! String || !RegExp(r'^[a-z][a-z0-9_]{0,47}$').hasMatch(value)) {
    throw FormatException('$context has an invalid node identifier.');
  }
}

String _protocolId(Map<String, Object?> json, String key, String context) {
  final value = _requiredString(json, key, context);
  if (!RegExp(r'^[A-Za-z][A-Za-z0-9._:-]{0,63}$').hasMatch(value)) {
    throw FormatException('$context.$key has an invalid identifier.');
  }
  return value;
}

T _enumByName<T extends Enum>(
  List<T> values,
  String name,
  String context,
) {
  for (final value in values) {
    if (value.name == name) return value;
  }
  throw FormatException('$context has unsupported value "$name".');
}

void _requireExactKeys(
  Map<String, Object?> source,
  Set<String> allowed,
  String context,
) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final missing = allowed.difference(source.keys.toSet());
  if (missing.isNotEmpty) {
    throw FormatException(
      '$context is missing required fields: ${missing.join(', ')}.',
    );
  }
}

void _requireRequiredAndAllowedKeys(
  Map<String, Object?> source,
  Set<String> required,
  Set<String> allowed,
  String context,
) {
  final unknown = source.keys.toSet().difference(allowed);
  if (unknown.isNotEmpty) {
    throw FormatException(
      '$context contains unsupported fields: ${unknown.join(', ')}.',
    );
  }
  final missing = required.difference(source.keys.toSet());
  if (missing.isNotEmpty) {
    throw FormatException(
      '$context is missing required fields: ${missing.join(', ')}.',
    );
  }
}

Map<String, Object?> _requiredObject(Object? value, String context) {
  if (value is! Map) {
    throw FormatException('$context must be an object.');
  }
  return value.cast<String, Object?>();
}

String _requiredString(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('$context.$key must be a non-empty string.');
  }
  return value;
}

int _requiredInt(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! int) {
    throw FormatException('$context.$key must be an integer.');
  }
  return value;
}

bool _requiredBool(
  Map<String, Object?> source,
  String key,
  String context,
) {
  final value = source[key];
  if (value is! bool) {
    throw FormatException('$context.$key must be a boolean.');
  }
  return value;
}

Map<String, Object?> _deepCopyMap(Map<String, Object?> source) {
  final encoded = jsonEncode(source);
  return (jsonDecode(encoded) as Map).cast<String, Object?>();
}

bool _isAsciiLetter(int rune) =>
    (rune >= 65 && rune <= 90) || (rune >= 97 && rune <= 122);
