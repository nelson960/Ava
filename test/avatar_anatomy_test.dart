import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_anatomy.dart';

void main() {
  late AvatarAnatomyMap anatomy;

  setUp(() {
    anatomy = AvatarAnatomyMap.fromJsonString(_anatomyJson());
  });

  test('uses barycentric triangle coverage to resolve anatomy', () {
    final surface = AvatarSurfaceReference.parse(
      '0 0 0 1 2 0.05 0.45 0.50',
    );

    expect(anatomy.regionFor(surface), AvatarAnatomicalRegion.leftHand);
  });

  test('surface identity stays stable independently of camera orientation', () {
    final frontViewSurface = AvatarSurfaceReference.parse(
      '0 0 0 1 2 0.05 0.45 0.50',
    );
    final sideViewSurface = AvatarSurfaceReference.parse(
      '0 0 2 1 0 0.50 0.45 0.05',
    );

    expect(
        anatomy.regionFor(frontViewSurface), AvatarAnatomicalRegion.leftHand);
    expect(anatomy.regionFor(sideViewSurface), AvatarAnatomicalRegion.leftHand);
  });

  test('interpolates bone influences and continuous local coordinates', () {
    final contact = anatomy.contactFor(
      AvatarSurfaceReference.parse(
        '0 0 0 1 2 0.05 0.45 0.50',
      ),
    );

    expect(contact.region, AvatarAnatomicalRegion.leftHand);
    expect(contact.primaryBone.jointName, 'LeftHand');
    expect(contact.primaryBone.weight, closeTo(0.95, 0.001));
    expect(
      contact.boneInfluences.fold<double>(
        0,
        (total, influence) => total + influence.weight,
      ),
      closeTo(1, 1e-9),
    );
    expect(contact.longitudinalPosition, closeTo(0.763, 0.002));
    expect(contact.radialAngle, closeTo(0, 0.001));
    expect(contact.surfaceSector, AvatarSurfaceSector.front);
  });

  test('resolves a skinned renderer pick from bone-local coordinates', () {
    final contact = anatomy.contactForJointCoordinates(
      jointName: 'LeftHand',
      longitudinalPosition: 0.82,
      radialAngle: 0,
    );

    expect(contact.region, AvatarAnatomicalRegion.leftHand);
    expect(contact.primaryBone.jointName, 'LeftHand');
    expect(contact.longitudinalPosition, 0.82);
    expect(contact.radialAngle, 0);
    expect(contact.surfaceSector, AvatarSurfaceSector.front);
  });

  test('exposes only joints backed by authored surface samples', () {
    expect(
      anatomy.interactiveJoints.map((joint) => joint.name),
      containsAll(<String>['Head', 'LeftHand', 'RightHand']),
    );
    expect(anatomy.jointFrame('LeftHand').axis.y, 1);
    expect(anatomy.renderableNodeNames, ['BodyNode']);
    expect(
      () => anatomy.contactForJointCoordinates(
        jointName: 'Missing',
        longitudinalPosition: 0.5,
        radialAngle: 0,
      ),
      throwsStateError,
    );
  });

  test('rejects stale surface indices instead of guessing', () {
    final surface = AvatarSurfaceReference.parse(
      '0 0 0 1 99 0.2 0.3 0.5',
    );

    expect(() => anatomy.regionFor(surface), throwsRangeError);
  });

  test('rejects malformed surface and anatomy data', () {
    expect(
      () => AvatarSurfaceReference.parse('0 0 1 2'),
      throwsFormatException,
    );
    expect(
      () => AvatarSurfaceReference.parse('0 0 0 1 2 0.2 0.3 0.1'),
      throwsFormatException,
    );
    expect(
      () => AvatarAnatomyMap.fromJsonString('{}'),
      throwsFormatException,
    );
  });
}

String _anatomyJson() {
  return jsonEncode({
    'schemaVersion': 2,
    'modelSha256': 'a' * 64,
    'regions': ['head', 'leftHand', 'rightHand'],
    'coordinateEncoding': {
      'longitudinal': 'uint16-normalized-zero-to-one',
      'radialAngle': 'uint16-normalized-minus-pi-to-pi',
      'jointWeights': 'four-uint8-normalized-sum-255',
    },
    'joints': [
      _joint('Head', 0),
      _joint('LeftHand', 1),
      _joint('RightHand', 2),
    ],
    'meshes': [
      {
        'name': 'Body',
        'nodeNames': ['BodyNode'],
        'primitives': [
          {
            'vertexCount': 4,
            'regionsU8': base64Encode(Uint8List.fromList([0, 1, 1, 2])),
            'dominantJointsU16LE': _u16([0, 1, 1, 2]),
            'jointIndicesU16LE': _u16([
              0,
              1,
              2,
              0,
              1,
              0,
              2,
              0,
              1,
              0,
              2,
              0,
              2,
              0,
              1,
              0,
            ]),
            'jointWeightsU8': base64Encode(
              Uint8List.fromList([
                255,
                0,
                0,
                0,
                255,
                0,
                0,
                0,
                255,
                0,
                0,
                0,
                255,
                0,
                0,
                0,
              ]),
            ),
            'longitudinalU16LE': _u16([0, 32768, 65535, 32768]),
            'radialAngleU16LE': _u16([
              _encodedAngleZero,
              _encodedAngleZero,
              _encodedAngleZero,
              _encodedAngleZero,
            ]),
          },
        ],
      },
    ],
  });
}

Map<String, Object> _joint(String name, int nodeIndex) => {
      'name': name,
      'nodeIndex': nodeIndex,
      'axis': [0, 1, 0],
      'front': [0, 0, 1],
      'tangent': [1, 0, 0],
      'length': 1,
    };

const _encodedAngleZero = 32768;

String _u16(List<int> values) {
  final bytes = Uint8List(values.length * 2);
  final data = ByteData.sublistView(bytes);
  for (var index = 0; index < values.length; index += 1) {
    data.setUint16(index * 2, values[index], Endian.little);
  }
  return base64Encode(bytes);
}
