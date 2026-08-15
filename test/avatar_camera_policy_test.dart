import 'package:flutter_test/flutter_test.dart';

import 'package:ava/state/ava_session_state.dart';
import 'package:ava/ui/avatar/avatar_camera_policy.dart';

void main() {
  test('recenters when a voice conversation begins', () {
    expect(
      AvatarCameraPolicy.shouldRecenterForConversation(
        AvaSessionPhase.idle,
        AvaSessionPhase.listening,
      ),
      isTrue,
    );
  });

  test('recenters when Ava begins speaking', () {
    expect(
      AvatarCameraPolicy.shouldRecenterForConversation(
        AvaSessionPhase.thinking,
        AvaSessionPhase.speaking,
      ),
      isTrue,
    );
  });

  test('does not interrupt intermediate or idle phase changes', () {
    expect(
      AvatarCameraPolicy.shouldRecenterForConversation(
        AvaSessionPhase.listening,
        AvaSessionPhase.thinking,
      ),
      isFalse,
    );
    expect(
      AvatarCameraPolicy.shouldRecenterForConversation(
        AvaSessionPhase.speaking,
        AvaSessionPhase.idle,
      ),
      isFalse,
    );
  });
}
