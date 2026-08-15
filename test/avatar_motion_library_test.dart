import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'package:ava/ui/avatar/avatar_motion_library.dart';

void main() {
  late String productionManifest;

  setUpAll(() {
    productionManifest =
        File('avatar/ava_motion_manifest.json').readAsStringSync();
  });

  test('production manifest registers only the reviewed response motions', () {
    final library = AvatarMotionLibrary.fromJsonString(productionManifest);

    expect(library.referenceActionName, 'AvaIdle');
    expect(
      library.clips.map((clip) => clip.id),
      const [
        'acknowledge_01',
        'explain_small_right_01',
        'compare_two_ideas_01',
      ],
    );
    expect(
      library.actionNames,
      const {
        'AvaMocap_acknowledge_01',
        'AvaMocap_explain_small_right_01',
        'AvaMocap_compare_two_ideas_01',
      },
    );
    expect(
      library.clip('acknowledge_01').duration,
      const Duration(milliseconds: 1500),
    );
    expect(
      library.clip('explain_small_right_01').duration,
      const Duration(milliseconds: 1500),
    );
    expect(
      library.clip('compare_two_ideas_01').duration,
      const Duration(seconds: 2),
    );
    expect(library.actionNames, isNot(contains('AvaExplain')));
  });

  test('unknown semantic motion capability fails loudly', () {
    final library = AvatarMotionLibrary.fromJsonString(productionManifest);

    expect(
      () => library.clip('wave_both_hands'),
      throwsA(
        isA<StateError>().having(
          (error) => error.message,
          'message',
          contains('not registered'),
        ),
      ),
    );
  });

  test('rejects stale, duplicate, and unsupported manifest data', () {
    final source = jsonDecode(productionManifest) as Map<String, dynamic>;

    Map<String, dynamic> copy() =>
        jsonDecode(jsonEncode(source)) as Map<String, dynamic>;

    final unknownField = copy()..['fallbackAnimation'] = 'AvaExplain';

    final duplicateClip = copy();
    final duplicateClips = duplicateClip['clips'] as List<dynamic>;
    duplicateClips.add(
      jsonDecode(jsonEncode(duplicateClips.first)) as Map<String, dynamic>,
    );

    final staleMarker = copy();
    final staleMarkers = ((staleMarker['clips'] as List<dynamic>).first
        as Map<String, dynamic>)['markers'] as Map<String, dynamic>;
    (staleMarkers['exit'] as Map<String, dynamic>)['endFrame'] = 999;

    for (final invalid in [unknownField, duplicateClip, staleMarker]) {
      expect(
        () => AvatarMotionLibrary.fromJsonString(jsonEncode(invalid)),
        throwsFormatException,
      );
    }
  });
}
