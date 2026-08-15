import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_rig_bindings.dart';

void main() {
  test('production rig binding asset maps every semantic bone uniquely', () {
    final bindings = AvatarRigBindings.fromJsonString(
      File('avatar/ava_rig_bindings.json').readAsStringSync(),
    );

    expect(bindings.requiredJointNames, hasLength(AvatarRigBone.values.length));
    expect(bindings.nameOf(AvatarRigBone.hips), 'J_Bip_C_Hips');
    expect(bindings.nameOf(AvatarRigBone.leftEye), 'J_Adj_L_FaceEye');
    expect(bindings.nameOf(AvatarRigBone.rightEye), 'J_Adj_R_FaceEye');
    expect(bindings.nameOf(AvatarRigBone.leftIndex3), 'J_Bip_L_Index3');
    expect(bindings.nameOf(AvatarRigBone.rightThumb2), 'J_Bip_R_Thumb2');
    expect(bindings.nameOf(AvatarRigBone.leftToe), 'J_Bip_L_ToeBase');
    expect(bindings.nameOf(AvatarRigBone.rightToe), 'J_Bip_R_ToeBase');
  });

  test('rejects missing, unknown, empty, and duplicate bone bindings', () {
    final source = jsonDecode(
      File('avatar/ava_rig_bindings.json').readAsStringSync(),
    ) as Map<String, dynamic>;
    final originalBones = source['bones'] as Map<String, dynamic>;

    Map<String, dynamic> document(Map<String, dynamic> bones) => {
          'schemaVersion': 1,
          'bones': bones,
        };

    final missing = Map<String, dynamic>.of(originalBones)..remove('head');
    final unknown = Map<String, dynamic>.of(originalBones)
      ..['unknown'] = 'Unknown';
    final empty = Map<String, dynamic>.of(originalBones)..['head'] = '  ';
    final duplicate = Map<String, dynamic>.of(originalBones)
      ..['head'] = originalBones['neck'];

    for (final invalid in [missing, unknown, empty, duplicate]) {
      expect(
        () => AvatarRigBindings.fromJsonString(jsonEncode(document(invalid))),
        throwsFormatException,
      );
    }
  });
}
