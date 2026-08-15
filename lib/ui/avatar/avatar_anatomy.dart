import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

enum AvatarAnatomicalRegion {
  hair,
  head,
  face,
  leftCheek,
  rightCheek,
  leftShoulder,
  rightShoulder,
  leftUpperArm,
  rightUpperArm,
  leftLowerArm,
  rightLowerArm,
  leftHand,
  rightHand,
  chest,
  waist,
  leftThigh,
  rightThigh,
  leftKnee,
  rightKnee,
  leftLowerLeg,
  rightLowerLeg,
  leftShoe,
  rightShoe,
}

enum AvatarBodySide { center, left, right }

extension AvatarAnatomicalRegionProperties on AvatarAnatomicalRegion {
  AvatarBodySide get side {
    if (name.startsWith('left')) return AvatarBodySide.left;
    if (name.startsWith('right')) return AvatarBodySide.right;
    return AvatarBodySide.center;
  }

  bool get usesProtectiveRepeatedReaction => switch (this) {
        AvatarAnatomicalRegion.chest ||
        AvatarAnatomicalRegion.waist ||
        AvatarAnatomicalRegion.leftThigh ||
        AvatarAnatomicalRegion.rightThigh =>
          true,
        _ => false,
      };
}

class AvatarModelVector3 {
  const AvatarModelVector3(this.x, this.y, this.z);

  final double x;
  final double y;
  final double z;
}

class AvatarSurfaceReference {
  const AvatarSurfaceReference({
    required this.meshIndex,
    required this.primitiveIndex,
    required this.vertexIndices,
    required this.barycentricWeights,
  });

  factory AvatarSurfaceReference.parse(String value) {
    final parts = value.trim().split(RegExp(r'\s+'));
    if (parts.length != 8) {
      throw FormatException(
        'Expected surface identity "mesh primitive a b c u v w"; '
        'received ${parts.length} values.',
      );
    }

    final meshIndex = int.tryParse(parts[0]);
    final primitiveIndex = int.tryParse(parts[1]);
    final vertexIndices =
        parts.sublist(2, 5).map(int.tryParse).toList(growable: false);
    final barycentricWeights =
        parts.sublist(5).map(double.tryParse).toList(growable: false);
    if (meshIndex == null || meshIndex < 0) {
      throw const FormatException('Surface mesh index must be non-negative.');
    }
    if (primitiveIndex == null || primitiveIndex < 0) {
      throw const FormatException(
        'Surface primitive index must be non-negative.',
      );
    }
    if (vertexIndices.any((index) => index == null || index < 0)) {
      throw const FormatException(
        'Surface vertex indices must be non-negative integers.',
      );
    }
    if (barycentricWeights.any(
      (weight) => weight == null || !weight.isFinite || weight < 0,
    )) {
      throw const FormatException(
        'Surface barycentric weights must be finite and non-negative.',
      );
    }
    final weights = barycentricWeights.cast<double>();
    final weightSum = weights.fold<double>(0, (sum, value) => sum + value);
    if ((weightSum - 1).abs() > 0.02) {
      throw FormatException(
        'Surface barycentric weights must sum to one; received $weightSum.',
      );
    }

    return AvatarSurfaceReference(
      meshIndex: meshIndex,
      primitiveIndex: primitiveIndex,
      vertexIndices: vertexIndices.cast<int>(),
      barycentricWeights: weights,
    );
  }

  final int meshIndex;
  final int primitiveIndex;
  final List<int> vertexIndices;
  final List<double> barycentricWeights;
}

enum AvatarSurfaceSector {
  front,
  tangentPositive,
  back,
  tangentNegative,
}

class AvatarBoneInfluence {
  const AvatarBoneInfluence({
    required this.jointIndex,
    required this.jointName,
    required this.weight,
  });

  final int jointIndex;
  final String jointName;
  final double weight;
}

class AvatarJointFrame {
  const AvatarJointFrame({
    required this.jointIndex,
    required this.name,
    required this.nodeIndex,
    required this.axis,
    required this.front,
    required this.tangent,
    required this.length,
  });

  final int jointIndex;
  final String name;
  final int nodeIndex;
  final AvatarModelVector3 axis;
  final AvatarModelVector3 front;
  final AvatarModelVector3 tangent;
  final double length;
}

/// Continuous anatomical information interpolated at an exact mesh contact.
class AvatarAnatomicalContact {
  const AvatarAnatomicalContact({
    required this.region,
    required this.boneInfluences,
    required this.longitudinalPosition,
    required this.radialAngle,
    required this.surfaceSector,
  });

  final AvatarAnatomicalRegion region;
  final List<AvatarBoneInfluence> boneInfluences;
  final double longitudinalPosition;
  final double radialAngle;
  final AvatarSurfaceSector surfaceSector;

  AvatarBoneInfluence get primaryBone => boneInfluences.first;
}

class AvatarAnatomyMap {
  AvatarAnatomyMap._({
    required this.modelSha256,
    required List<AvatarAnatomicalRegion> regionTable,
    required List<AvatarJointFrame> joints,
    required List<String> renderableNodeNames,
    required List<List<_AvatarPrimitiveAnatomy>> meshPrimitives,
  })  : _regionTable = List.unmodifiable(regionTable),
        _joints = List.unmodifiable(joints),
        renderableNodeNames = List.unmodifiable(renderableNodeNames),
        _meshPrimitives = List<List<_AvatarPrimitiveAnatomy>>.unmodifiable(
          meshPrimitives.map<List<_AvatarPrimitiveAnatomy>>(
            (mesh) => List<_AvatarPrimitiveAnatomy>.unmodifiable(mesh),
          ),
        );

  factory AvatarAnatomyMap.fromJsonString(String source) {
    final decoded = jsonDecode(source);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('Avatar anatomy map must be a JSON object.');
    }
    if (decoded['schemaVersion'] != 2) {
      throw FormatException(
        'Unsupported avatar anatomy schema: ${decoded['schemaVersion']}.',
      );
    }
    final modelSha256 = decoded['modelSha256'];
    if (modelSha256 is! String ||
        !RegExp(r'^[a-f0-9]{64}$').hasMatch(modelSha256)) {
      throw const FormatException(
        'Avatar anatomy map has an invalid model SHA-256.',
      );
    }

    final rawRegions = decoded['regions'];
    if (rawRegions is! List || rawRegions.isEmpty) {
      throw const FormatException(
        'Avatar anatomy map must declare its region table.',
      );
    }
    final regionTable = rawRegions.map((rawName) {
      if (rawName is! String) {
        throw const FormatException('Avatar region names must be strings.');
      }
      try {
        return AvatarAnatomicalRegion.values.byName(rawName);
      } on ArgumentError {
        throw FormatException('Unknown avatar anatomical region $rawName.');
      }
    }).toList(growable: false);
    if (regionTable.toSet().length != regionTable.length) {
      throw const FormatException(
        'Avatar anatomy region table contains duplicates.',
      );
    }

    final rawEncoding = decoded['coordinateEncoding'];
    if (rawEncoding is! Map<String, dynamic> ||
        rawEncoding['longitudinal'] != 'uint16-normalized-zero-to-one' ||
        rawEncoding['radialAngle'] != 'uint16-normalized-minus-pi-to-pi' ||
        rawEncoding['jointWeights'] != 'four-uint8-normalized-sum-255') {
      throw const FormatException(
        'Avatar anatomy map uses an unsupported coordinate encoding.',
      );
    }

    final rawJoints = decoded['joints'];
    if (rawJoints is! List || rawJoints.isEmpty) {
      throw const FormatException(
        'Avatar anatomy map must declare its skin joints.',
      );
    }
    final joints = rawJoints.indexed.map((entry) {
      final jointIndex = entry.$1;
      final rawJoint = entry.$2;
      if (rawJoint is! Map<String, dynamic>) {
        throw const FormatException('Avatar joints must be objects.');
      }
      final name = rawJoint['name'];
      final nodeIndex = rawJoint['nodeIndex'];
      final length = rawJoint['length'];
      if (name is! String || name.trim().isEmpty) {
        throw const FormatException('Avatar joint names must be non-empty.');
      }
      if (nodeIndex is! int || nodeIndex < 0) {
        throw const FormatException(
          'Avatar joint node indices must be non-negative.',
        );
      }
      if (length is! num || !length.isFinite || length <= 0) {
        throw const FormatException(
          'Avatar joint lengths must be finite and positive.',
        );
      }
      final axis = _parseVector(rawJoint['axis'], 'joint $name axis');
      final front = _parseVector(rawJoint['front'], 'joint $name front');
      final tangent = _parseVector(
        rawJoint['tangent'],
        'joint $name tangent',
      );
      return AvatarJointFrame(
        jointIndex: jointIndex,
        name: name,
        nodeIndex: nodeIndex,
        axis: axis,
        front: front,
        tangent: tangent,
        length: length.toDouble(),
      );
    }).toList(growable: false);
    if (joints.map((joint) => joint.name).toSet().length != joints.length) {
      throw const FormatException(
        'Avatar anatomy joint names must be unique.',
      );
    }

    final rawMeshes = decoded['meshes'];
    if (rawMeshes is! List || rawMeshes.isEmpty) {
      throw const FormatException(
        'Avatar anatomy map must contain at least one mesh.',
      );
    }
    final renderableNodeNames = <String>[];
    final meshPrimitives = rawMeshes.map((rawMesh) {
      if (rawMesh is! Map<String, dynamic>) {
        throw const FormatException('Avatar mesh entries must be objects.');
      }
      final rawNodeNames = rawMesh['nodeNames'];
      if (rawNodeNames is! List ||
          rawNodeNames.isEmpty ||
          rawNodeNames.any(
            (name) => name is! String || name.trim().isEmpty,
          )) {
        throw const FormatException(
          'Every avatar mesh must declare its non-empty GLB node names.',
        );
      }
      renderableNodeNames.addAll(rawNodeNames.cast<String>());
      final rawPrimitives = rawMesh['primitives'];
      if (rawPrimitives is! List || rawPrimitives.isEmpty) {
        throw const FormatException(
          'Every avatar mesh must contain at least one primitive.',
        );
      }
      return rawPrimitives.map((rawPrimitive) {
        if (rawPrimitive is! Map<String, dynamic>) {
          throw const FormatException(
            'Avatar primitive entries must be objects.',
          );
        }
        final vertexCount = rawPrimitive['vertexCount'];
        if (vertexCount is! int || vertexCount <= 0) {
          throw const FormatException(
            'Avatar primitive vertex count must be positive.',
          );
        }
        final primitive = _AvatarPrimitiveAnatomy.decode(
          rawPrimitive,
          vertexCount: vertexCount,
          regionCount: regionTable.length,
          jointCount: joints.length,
        );
        return primitive;
      }).toList(growable: false);
    }).toList(growable: false);
    if (renderableNodeNames.toSet().length != renderableNodeNames.length) {
      throw const FormatException(
        'Avatar renderable GLB node names must be unique.',
      );
    }

    return AvatarAnatomyMap._(
      modelSha256: modelSha256,
      regionTable: regionTable,
      joints: joints,
      renderableNodeNames: renderableNodeNames,
      meshPrimitives: meshPrimitives,
    );
  }

  final String modelSha256;
  final List<String> renderableNodeNames;
  final List<AvatarAnatomicalRegion> _regionTable;
  final List<AvatarJointFrame> _joints;
  final List<List<_AvatarPrimitiveAnatomy>> _meshPrimitives;

  AvatarAnatomicalRegion regionFor(AvatarSurfaceReference surface) {
    return contactFor(surface).region;
  }

  List<AvatarJointFrame> get interactiveJoints => List.unmodifiable(
        _joints.where(
          (joint) => _meshPrimitives.any(
            (mesh) => mesh.any(
              (primitive) => primitive.hasDominantJoint(joint.jointIndex),
            ),
          ),
        ),
      );

  AvatarJointFrame jointFrame(String jointName) {
    final index = _joints.indexWhere((joint) => joint.name == jointName);
    if (index < 0) {
      throw StateError('Avatar anatomy does not contain joint $jointName.');
    }
    return _joints[index];
  }

  /// Resolves a renderer pick expressed in the selected bone's local frame.
  ///
  /// Filament exposes the current skinned surface depth and bone transforms,
  /// but not the source triangle. Anatomy Map v2 therefore classifies the
  /// contact against the closest authored vertex samples in continuous
  /// longitudinal/radial bone coordinates. This stays stable while the mesh
  /// animates and while the camera moves around Ava.
  AvatarAnatomicalContact contactForJointCoordinates({
    required String jointName,
    required double longitudinalPosition,
    required double radialAngle,
  }) {
    if (!longitudinalPosition.isFinite ||
        longitudinalPosition < 0 ||
        longitudinalPosition > 1) {
      throw ArgumentError.value(
        longitudinalPosition,
        'longitudinalPosition',
        'must be finite and between zero and one',
      );
    }
    if (!radialAngle.isFinite) {
      throw ArgumentError.value(
        radialAngle,
        'radialAngle',
        'must be finite',
      );
    }
    final frame = jointFrame(jointName);
    final normalizedAngle = _normalizedAngle(radialAngle);
    final candidates = <_AvatarJointSample>[];
    for (final mesh in _meshPrimitives) {
      for (final primitive in mesh) {
        for (var vertex = 0; vertex < primitive.vertexCount; vertex += 1) {
          if (primitive.dominantJointAt(vertex) != frame.jointIndex) continue;
          final longitudinalDelta =
              primitive.longitudinalAt(vertex) - longitudinalPosition;
          final radialDelta = _normalizedAngle(
            primitive.radialAngleAt(vertex) - normalizedAngle,
          );
          final score = longitudinalDelta * longitudinalDelta * 6 +
              (radialDelta / math.pi) * (radialDelta / math.pi);
          candidates.add(
            _AvatarJointSample(
              score: score,
              vertex: vertex,
              primitive: primitive,
            ),
          );
        }
      }
    }
    if (candidates.isEmpty) {
      throw StateError(
        'Avatar joint $jointName has no authored surface samples.',
      );
    }
    candidates.sort((left, right) => left.score.compareTo(right.score));

    final regionScores = <int, double>{};
    final jointScores = <int, double>{};
    for (final candidate in candidates.take(math.min(16, candidates.length))) {
      final sampleWeight = 1 / (candidate.score + 0.000001);
      final primitive = candidate.primitive;
      final vertex = candidate.vertex;
      regionScores.update(
        primitive.regionAt(vertex),
        (score) => score + sampleWeight,
        ifAbsent: () => sampleWeight,
      );
      for (var influence = 0; influence < 4; influence += 1) {
        final weight =
            sampleWeight * primitive.jointWeightAt(vertex, influence);
        if (weight <= 0) continue;
        jointScores.update(
          primitive.jointAt(vertex, influence),
          (score) => score + weight,
          ifAbsent: () => weight,
        );
      }
    }

    final winningRegion = regionScores.entries.reduce((winner, candidate) {
      if (candidate.value > winner.value) return candidate;
      if (candidate.value == winner.value && candidate.key < winner.key) {
        return candidate;
      }
      return winner;
    }).key;
    final sortedJoints = jointScores.entries.toList(growable: false)
      ..sort((left, right) {
        final weightOrder = right.value.compareTo(left.value);
        return weightOrder != 0 ? weightOrder : left.key.compareTo(right.key);
      });
    final selectedJoints = sortedJoints.take(4).toList(growable: false);
    final total = selectedJoints.fold<double>(
      0,
      (sum, influence) => sum + influence.value,
    );
    if (total <= 0) {
      throw StateError(
        'Avatar joint $jointName resolved without a positive skin weight.',
      );
    }
    return AvatarAnatomicalContact(
      region: _regionTable[winningRegion],
      boneInfluences: List.unmodifiable(
        selectedJoints.map(
          (influence) => AvatarBoneInfluence(
            jointIndex: influence.key,
            jointName: _joints[influence.key].name,
            weight: influence.value / total,
          ),
        ),
      ),
      longitudinalPosition: longitudinalPosition,
      radialAngle: normalizedAngle,
      surfaceSector: _surfaceSector(normalizedAngle),
    );
  }

  AvatarAnatomicalContact contactFor(AvatarSurfaceReference surface) {
    if (surface.meshIndex >= _meshPrimitives.length) {
      throw RangeError.index(
        surface.meshIndex,
        _meshPrimitives,
        'surface.meshIndex',
      );
    }
    final primitives = _meshPrimitives[surface.meshIndex];
    if (surface.primitiveIndex >= primitives.length) {
      throw RangeError.index(
        surface.primitiveIndex,
        primitives,
        'surface.primitiveIndex',
      );
    }
    final primitive = primitives[surface.primitiveIndex];
    final regionScores = <int, double>{};
    final jointScores = <int, double>{};
    for (var index = 0; index < 3; index += 1) {
      final vertexIndex = surface.vertexIndices[index];
      if (vertexIndex >= primitive.vertexCount) {
        throw RangeError.index(
          vertexIndex,
          List<void>.filled(primitive.vertexCount, null),
          'surface.vertexIndices[$index]',
        );
      }
      final barycentricWeight = surface.barycentricWeights[index];
      final regionIndex = primitive.regionAt(vertexIndex);
      regionScores.update(
        regionIndex,
        (score) => score + barycentricWeight,
        ifAbsent: () => barycentricWeight,
      );
      for (var influence = 0; influence < 4; influence += 1) {
        final jointIndex = primitive.jointAt(vertexIndex, influence);
        final weight =
            barycentricWeight * primitive.jointWeightAt(vertexIndex, influence);
        if (weight <= 0) continue;
        jointScores.update(
          jointIndex,
          (score) => score + weight,
          ifAbsent: () => weight,
        );
      }
    }

    final winningRegion = regionScores.entries.reduce((winner, candidate) {
      if (candidate.value > winner.value) return candidate;
      if (candidate.value == winner.value && candidate.key < winner.key) {
        return candidate;
      }
      return winner;
    }).key;
    final sortedJoints = jointScores.entries.toList(growable: false)
      ..sort((left, right) {
        final byWeight = right.value.compareTo(left.value);
        return byWeight != 0 ? byWeight : left.key.compareTo(right.key);
      });
    if (sortedJoints.isEmpty) {
      throw StateError('Avatar contact resolved without any bone influence.');
    }
    final selectedJoints = sortedJoints.take(4).toList(growable: false);
    final selectedInfluenceTotal = selectedJoints.fold<double>(
      0,
      (total, entry) => total + entry.value,
    );
    final boneInfluences = selectedJoints.map((entry) {
      return AvatarBoneInfluence(
        jointIndex: entry.key,
        jointName: _joints[entry.key].name,
        weight: entry.value / selectedInfluenceTotal,
      );
    }).toList(growable: false);

    final primaryJoint = boneInfluences.first.jointIndex;
    var coordinateWeight = 0.0;
    var longitudinal = 0.0;
    var radialSin = 0.0;
    var radialCos = 0.0;
    for (var index = 0; index < 3; index += 1) {
      final vertexIndex = surface.vertexIndices[index];
      if (primitive.dominantJointAt(vertexIndex) != primaryJoint) continue;
      final weight = surface.barycentricWeights[index];
      final angle = primitive.radialAngleAt(vertexIndex);
      coordinateWeight += weight;
      longitudinal += primitive.longitudinalAt(vertexIndex) * weight;
      radialSin += math.sin(angle) * weight;
      radialCos += math.cos(angle) * weight;
    }
    if (coordinateWeight <= 0) {
      throw StateError(
        'Avatar contact could not interpolate its primary bone coordinates.',
      );
    }
    longitudinal /= coordinateWeight;
    final radialAngle = math.atan2(
      radialSin / coordinateWeight,
      radialCos / coordinateWeight,
    );
    return AvatarAnatomicalContact(
      region: _regionTable[winningRegion],
      boneInfluences: List.unmodifiable(boneInfluences),
      longitudinalPosition: longitudinal,
      radialAngle: radialAngle,
      surfaceSector: _surfaceSector(radialAngle),
    );
  }
}

class _AvatarJointSample {
  const _AvatarJointSample({
    required this.score,
    required this.vertex,
    required this.primitive,
  });

  final double score;
  final int vertex;
  final _AvatarPrimitiveAnatomy primitive;
}

class _AvatarPrimitiveAnatomy {
  const _AvatarPrimitiveAnatomy._({
    required this.vertexCount,
    required Uint8List regions,
    required Uint8List dominantJoints,
    required Uint8List jointIndices,
    required Uint8List jointWeights,
    required Uint8List longitudinal,
    required Uint8List radialAngle,
  })  : _regions = regions,
        _dominantJoints = dominantJoints,
        _jointIndices = jointIndices,
        _jointWeights = jointWeights,
        _longitudinal = longitudinal,
        _radialAngle = radialAngle;

  factory _AvatarPrimitiveAnatomy.decode(
    Map<String, dynamic> source, {
    required int vertexCount,
    required int regionCount,
    required int jointCount,
  }) {
    final regions = _decodeBytes(
      source['regionsU8'],
      'regionsU8',
      vertexCount,
    );
    final dominantJoints = _decodeBytes(
      source['dominantJointsU16LE'],
      'dominantJointsU16LE',
      vertexCount * 2,
    );
    final jointIndices = _decodeBytes(
      source['jointIndicesU16LE'],
      'jointIndicesU16LE',
      vertexCount * 4 * 2,
    );
    final jointWeights = _decodeBytes(
      source['jointWeightsU8'],
      'jointWeightsU8',
      vertexCount * 4,
    );
    final longitudinal = _decodeBytes(
      source['longitudinalU16LE'],
      'longitudinalU16LE',
      vertexCount * 2,
    );
    final radialAngle = _decodeBytes(
      source['radialAngleU16LE'],
      'radialAngleU16LE',
      vertexCount * 2,
    );
    final primitive = _AvatarPrimitiveAnatomy._(
      vertexCount: vertexCount,
      regions: regions,
      dominantJoints: dominantJoints,
      jointIndices: jointIndices,
      jointWeights: jointWeights,
      longitudinal: longitudinal,
      radialAngle: radialAngle,
    );
    for (var vertex = 0; vertex < vertexCount; vertex += 1) {
      if (primitive.regionAt(vertex) >= regionCount) {
        throw FormatException(
          'Avatar vertex $vertex references an invalid anatomical region.',
        );
      }
      if (primitive.dominantJointAt(vertex) >= jointCount) {
        throw FormatException(
          'Avatar vertex $vertex references an invalid dominant joint.',
        );
      }
      var weightSum = 0;
      for (var influence = 0; influence < 4; influence += 1) {
        if (primitive.jointAt(vertex, influence) >= jointCount) {
          throw FormatException(
            'Avatar vertex $vertex references an invalid skin joint.',
          );
        }
        weightSum += primitive._jointWeightByteAt(vertex, influence);
      }
      if (weightSum != 255) {
        throw FormatException(
          'Avatar vertex $vertex skin weights sum to $weightSum instead of 255.',
        );
      }
    }
    return primitive;
  }

  final int vertexCount;
  final Uint8List _regions;
  final Uint8List _dominantJoints;
  final Uint8List _jointIndices;
  final Uint8List _jointWeights;
  final Uint8List _longitudinal;
  final Uint8List _radialAngle;

  int regionAt(int vertex) => _regions[vertex];
  int dominantJointAt(int vertex) => _readUint16(_dominantJoints, vertex);
  int jointAt(int vertex, int influence) =>
      _readUint16(_jointIndices, vertex * 4 + influence);
  int _jointWeightByteAt(int vertex, int influence) =>
      _jointWeights[vertex * 4 + influence];
  double jointWeightAt(int vertex, int influence) =>
      _jointWeightByteAt(vertex, influence) / 255;
  double longitudinalAt(int vertex) =>
      _readUint16(_longitudinal, vertex) / 65535;
  double radialAngleAt(int vertex) =>
      (_readUint16(_radialAngle, vertex) / 65535) * 2 * math.pi - math.pi;

  bool hasDominantJoint(int jointIndex) {
    for (var vertex = 0; vertex < vertexCount; vertex += 1) {
      if (dominantJointAt(vertex) == jointIndex) return true;
    }
    return false;
  }
}

AvatarSurfaceSector _surfaceSector(double angle) {
  final quarter = math.pi / 4;
  if (angle >= -quarter && angle < quarter) {
    return AvatarSurfaceSector.front;
  }
  if (angle >= quarter && angle < 3 * quarter) {
    return AvatarSurfaceSector.tangentPositive;
  }
  if (angle >= 3 * quarter || angle < -3 * quarter) {
    return AvatarSurfaceSector.back;
  }
  return AvatarSurfaceSector.tangentNegative;
}

double _normalizedAngle(double angle) {
  var normalized = angle % (2 * math.pi);
  if (normalized > math.pi) normalized -= 2 * math.pi;
  if (normalized < -math.pi) normalized += 2 * math.pi;
  return normalized;
}

AvatarModelVector3 _parseVector(Object? raw, String fieldName) {
  if (raw is! List || raw.length != 3) {
    throw FormatException('$fieldName must contain three numbers.');
  }
  final values = raw.map((value) {
    if (value is! num || !value.isFinite) {
      throw FormatException('$fieldName must contain finite numbers.');
    }
    return value.toDouble();
  }).toList(growable: false);
  return AvatarModelVector3(values[0], values[1], values[2]);
}

Uint8List _decodeBytes(Object? raw, String fieldName, int expectedLength) {
  if (raw is! String || raw.isEmpty) {
    throw FormatException('$fieldName must be a base64 string.');
  }
  late final Uint8List bytes;
  try {
    bytes = base64Decode(raw);
  } on FormatException {
    throw FormatException('$fieldName is not valid base64.');
  }
  if (bytes.length != expectedLength) {
    throw FormatException(
      '$fieldName decoded to ${bytes.length} bytes; expected $expectedLength.',
    );
  }
  return bytes;
}

int _readUint16(Uint8List bytes, int elementIndex) {
  final byteOffset = elementIndex * 2;
  return bytes[byteOffset] | (bytes[byteOffset + 1] << 8);
}
