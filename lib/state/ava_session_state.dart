import '../models/ava_performance_plan.dart';
import '../models/lesson_response.dart';

enum AvaSessionPhase {
  idle,
  listening,
  thinking,
  speaking,
  failure,
}

enum AvaConnectionHealth {
  disconnected,
  preparing,
  connected,
  failed,
}

enum AvaCanvasSessionState {
  hidden,
  visible,
  rejected,
}

enum AvaMemorySessionState {
  synchronized,
  retryRequired,
}

class AvaSessionState {
  const AvaSessionState({
    this.turnState = AvaSessionPhase.idle,
    this.userMicIntent = false,
    this.actualTrackEnabled = false,
    this.connectionHealth = AvaConnectionHealth.disconnected,
    this.canvasState = AvaCanvasSessionState.hidden,
    this.memoryState = AvaMemorySessionState.synchronized,
    this.memoryWarning,
    this.childMessage,
    this.response,
    this.performance,
    this.turnId,
    this.activeSegmentId,
    this.errorMessage,
  });

  final AvaSessionPhase turnState;
  final bool userMicIntent;
  final bool actualTrackEnabled;
  final AvaConnectionHealth connectionHealth;
  final AvaCanvasSessionState canvasState;
  final AvaMemorySessionState memoryState;
  final String? memoryWarning;
  final String? childMessage;
  final LessonResponse? response;
  final AvaPerformancePlan? performance;
  final String? turnId;
  final String? activeSegmentId;
  final String? errorMessage;

  bool get isBusy =>
      userMicIntent ||
      switch (turnState) {
        AvaSessionPhase.listening ||
        AvaSessionPhase.thinking ||
        AvaSessionPhase.speaking =>
          true,
        AvaSessionPhase.idle || AvaSessionPhase.failure => false,
      };

  String? get caption => response?.speech;

  AvaSessionState copyWith({
    AvaSessionPhase? turnState,
    bool? userMicIntent,
    bool? actualTrackEnabled,
    AvaConnectionHealth? connectionHealth,
    AvaCanvasSessionState? canvasState,
    AvaMemorySessionState? memoryState,
    String? memoryWarning,
    String? childMessage,
    LessonResponse? response,
    AvaPerformancePlan? performance,
    String? turnId,
    String? activeSegmentId,
    String? errorMessage,
    bool clearChildMessage = false,
    bool clearResponse = false,
    bool clearPerformance = false,
    bool clearTurn = false,
    bool clearActiveSegment = false,
    bool clearError = false,
    bool clearMemoryWarning = false,
  }) {
    return AvaSessionState(
      turnState: turnState ?? this.turnState,
      userMicIntent: userMicIntent ?? this.userMicIntent,
      actualTrackEnabled: actualTrackEnabled ?? this.actualTrackEnabled,
      connectionHealth: connectionHealth ?? this.connectionHealth,
      canvasState: canvasState ?? this.canvasState,
      memoryState: memoryState ?? this.memoryState,
      memoryWarning:
          clearMemoryWarning ? null : memoryWarning ?? this.memoryWarning,
      childMessage:
          clearChildMessage ? null : childMessage ?? this.childMessage,
      response: clearResponse ? null : response ?? this.response,
      performance: clearPerformance ? null : performance ?? this.performance,
      turnId: clearTurn ? null : turnId ?? this.turnId,
      activeSegmentId:
          clearActiveSegment ? null : activeSegmentId ?? this.activeSegmentId,
      errorMessage: clearError ? null : errorMessage ?? this.errorMessage,
    );
  }
}
