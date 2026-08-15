import 'dart:convert';

enum AvatarRigBone {
  hips,
  spine,
  chest,
  upperChest,
  neck,
  head,
  leftEye,
  rightEye,
  leftShoulder,
  leftUpperArm,
  leftLowerArm,
  leftHand,
  leftThumb1,
  leftThumb2,
  leftThumb3,
  leftIndex1,
  leftIndex2,
  leftIndex3,
  leftMiddle1,
  leftMiddle2,
  leftMiddle3,
  leftRing1,
  leftRing2,
  leftRing3,
  leftLittle1,
  leftLittle2,
  leftLittle3,
  rightShoulder,
  rightUpperArm,
  rightLowerArm,
  rightHand,
  rightThumb1,
  rightThumb2,
  rightThumb3,
  rightIndex1,
  rightIndex2,
  rightIndex3,
  rightMiddle1,
  rightMiddle2,
  rightMiddle3,
  rightRing1,
  rightRing2,
  rightRing3,
  rightLittle1,
  rightLittle2,
  rightLittle3,
  leftUpperLeg,
  leftLowerLeg,
  leftFoot,
  leftToe,
  rightUpperLeg,
  rightLowerLeg,
  rightFoot,
  rightToe,
}

class AvatarRigBindings {
  AvatarRigBindings._(this._boneNames);

  factory AvatarRigBindings.fromJsonString(String source) {
    final decoded = jsonDecode(source);
    if (decoded is! Map<String, dynamic> || decoded['schemaVersion'] != 1) {
      throw const FormatException(
        'Ava rig bindings must use schema version 1.',
      );
    }
    final rawBones = decoded['bones'];
    if (rawBones is! Map<String, dynamic>) {
      throw const FormatException('Ava rig bindings must declare bones.');
    }
    final expectedKeys = AvatarRigBone.values.map((bone) => bone.name).toSet();
    final actualKeys = rawBones.keys.toSet();
    if (actualKeys.length != expectedKeys.length ||
        !actualKeys.containsAll(expectedKeys)) {
      throw FormatException(
        'Ava rig bindings mismatch: missing='
        '${expectedKeys.difference(actualKeys).toList()..sort()}, '
        'unexpected=${actualKeys.difference(expectedKeys).toList()..sort()}.',
      );
    }
    final bindings = <AvatarRigBone, String>{};
    for (final bone in AvatarRigBone.values) {
      final name = rawBones[bone.name];
      if (name is! String || name.trim().isEmpty) {
        throw FormatException(
          'Ava rig bone ${bone.name} must have a non-empty GLB joint name.',
        );
      }
      bindings[bone] = name;
    }
    if (bindings.values.toSet().length != bindings.length) {
      throw const FormatException('Ava rig bone bindings must be unique.');
    }
    return AvatarRigBindings._(Map.unmodifiable(bindings));
  }

  final Map<AvatarRigBone, String> _boneNames;

  String nameOf(AvatarRigBone bone) => _boneNames[bone]!;

  Set<String> get requiredJointNames => Set.unmodifiable(_boneNames.values);
}
