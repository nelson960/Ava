import '../../state/ava_session_state.dart';

abstract final class AvatarCameraPolicy {
  static bool shouldRecenterForConversation(
    AvaSessionPhase? previous,
    AvaSessionPhase next,
  ) {
    return next == AvaSessionPhase.listening ||
        (next == AvaSessionPhase.speaking &&
            previous != AvaSessionPhase.speaking);
  }
}
