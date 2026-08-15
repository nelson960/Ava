import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../canvas/ava_canvas_engine.dart';
import '../config/ava_backend_configuration.dart';
import '../models/lesson_response.dart';
import '../models/local_learning_activity.dart';
import '../models/learning_progress.dart';
import '../models/ava_realtime_protocol.dart';
import '../services/ava_memory_control_service.dart';
import '../services/learning_progress_service.dart';
import '../services/memory_service.dart';
import '../services/pcm_speech_output_service.dart';
import '../services/ava_realtime_presentation.dart';
import '../services/ava_realtime_bootstrap_service.dart';
import '../services/ava_realtime_runtime_factory.dart';
import '../services/ava_realtime_session.dart';
import '../services/ava_realtime_webrtc.dart';
import 'ava_realtime_session_controller.dart';
import 'ava_session_state.dart';

final volumeProvider = StateProvider<double>((ref) => 0.8);

final avaCanvasEngineProvider = ChangeNotifierProvider<AvaCanvasEngine>((ref) {
  return AvaCanvasEngine();
});

final avaRealtimePresentationProvider =
    ChangeNotifierProvider<AvaRealtimePresentationController>((ref) {
  return AvaRealtimePresentationController();
});

final localLearningActivityCatalogProvider =
    FutureProvider<LocalLearningActivityCatalog>((ref) {
  return LocalLearningActivityCatalog.load(rootBundle);
});

final databaseProvider = Provider<AppDatabase>((ref) {
  final database = AppDatabase();
  ref.onDispose(database.close);
  return database;
});

final avaBackendConfigurationProvider =
    Provider<AvaBackendConfiguration>((ref) {
  return AvaBackendConfiguration.fromEnvironment();
});

final avaMemoryControlTransportProvider =
    Provider<AvaMemoryControlTransport>((ref) {
  final transport = IoAvaMemoryControlTransport();
  ref.onDispose(transport.close);
  return transport;
});

final avaMemoryControlProvider = Provider<AvaMemoryControl>((ref) {
  return ProductionAvaMemoryControl(
    configuration: ref.watch(avaBackendConfigurationProvider),
    transport: ref.watch(avaMemoryControlTransportProvider),
  );
});

final avaRealtimeBootstrapTransportProvider =
    Provider<AvaRealtimeBootstrapTransport>((ref) {
  final transport = IoAvaRealtimeBootstrapTransport();
  ref.onDispose(transport.close);
  return transport;
});

final avaRealtimeBootstrapServiceProvider =
    Provider<AvaRealtimeBootstrapService>((ref) {
  return AvaRealtimeBootstrapService(
    configuration: ref.watch(avaBackendConfigurationProvider),
    transport: ref.watch(avaRealtimeBootstrapTransportProvider),
  );
});

final avaRealtimeTransportConnectorProvider =
    Provider<AvaRealtimeTransportConnector>((ref) {
  return (bootstrap) => IoAvaRealtimeTransport.connect(bootstrap);
});

final avaRealtimeSdpTransportProvider =
    Provider<AvaRealtimeSdpTransport>((ref) {
  final transport = IoAvaRealtimeSdpTransport();
  ref.onDispose(transport.close);
  return transport;
});

final avaRealtimeSdpServiceProvider = Provider<AvaRealtimeSdpService>((ref) {
  return AvaRealtimeSdpService(
    transport: ref.watch(avaRealtimeSdpTransportProvider),
    requestTimeout: ref.watch(avaBackendConfigurationProvider).requestTimeout,
  );
});

final avaRealtimeMediaConnectorProvider =
    Provider<AvaRealtimeMediaConnector>((ref) {
  return createAvaRealtimeMediaConnector(
    mediaSessionFactory: FlutterAvaRealtimeMediaSession.create,
    sdpService: ref.watch(avaRealtimeSdpServiceProvider),
  );
});

final avaRealtimeRuntimeFactoryProvider =
    Provider<AvaRealtimeRuntimeFactory>((ref) {
  final factory = AvaRealtimeRuntimeFactory(
    bootstrapService: ref.watch(avaRealtimeBootstrapServiceProvider),
    mediaConnector: ref.watch(avaRealtimeMediaConnectorProvider),
    transportConnector: ref.watch(avaRealtimeTransportConnectorProvider),
    speechOutput: ref.watch(pcmSpeechOutputServiceProvider),
    canvasEngine: ref.watch(avaCanvasEngineProvider.notifier),
    presentation: ref.watch(avaRealtimePresentationProvider.notifier),
    cancelRequestIdFactory: createSecureAvaCancelRequestId,
  );
  ref.onDispose(() => unawaited(factory.close()));
  return factory;
});

final pcmSpeechOutputServiceProvider = Provider<PcmSpeechOutputService>((ref) {
  return const ApplePcmSpeechOutputService();
});

final learningProgressServiceProvider =
    Provider<LearningProgressService>((ref) {
  return LearningProgressService(
    ref.watch(databaseProvider),
    profileId: StudentStateNotifier.profileId,
  );
});

final learningProgressSummaryProvider =
    StreamProvider<LearningProgressSummary>((ref) {
  return ref.watch(learningProgressServiceProvider).watchSummary();
});

class StudentState {
  const StudentState({
    this.name = 'Tiny Explorer',
    this.dateOfBirth,
    this.captionsEnabled = false,
    this.mathScore = 0,
    this.scienceScore = 0,
    this.isLoading = false,
    this.errorMessage,
  });

  final String name;
  final DateTime? dateOfBirth;
  final bool captionsEnabled;
  final int mathScore;
  final int scienceScore;
  final bool isLoading;
  final String? errorMessage;

  StudentState copyWith({
    String? name,
    DateTime? dateOfBirth,
    bool? captionsEnabled,
    int? mathScore,
    int? scienceScore,
    bool? isLoading,
    String? errorMessage,
    bool clearError = false,
  }) {
    return StudentState(
      name: name ?? this.name,
      dateOfBirth: dateOfBirth ?? this.dateOfBirth,
      captionsEnabled: captionsEnabled ?? this.captionsEnabled,
      mathScore: mathScore ?? this.mathScore,
      scienceScore: scienceScore ?? this.scienceScore,
      isLoading: isLoading ?? this.isLoading,
      errorMessage: clearError ? null : errorMessage ?? this.errorMessage,
    );
  }
}

class StudentStateNotifier extends StateNotifier<StudentState> {
  StudentStateNotifier(this._database)
      : super(const StudentState(isLoading: true)) {
    _loadProfile();
  }

  static const profileId = 'current_child';
  final AppDatabase _database;

  Future<void> _loadProfile() async {
    try {
      final profile = await _database.getProfile(profileId);
      if (profile == null) {
        final defaultProfile = _defaultProfile();
        await _database.insertOrUpdateProfile(defaultProfile);
        state = const StudentState(name: 'Tiny Explorer');
        return;
      }
      state = StudentState(
        name: profile.name,
        dateOfBirth: profile.dateOfBirth,
        captionsEnabled: profile.captionsEnabled,
        mathScore: profile.mathScore,
        scienceScore: profile.scienceScore,
      );
    } catch (error) {
      state = state.copyWith(
        isLoading: false,
        errorMessage: 'Could not load the child profile: $error',
      );
    }
  }

  Future<void> updateName(String newName) async {
    final normalizedName = newName.trim();
    if (normalizedName.isEmpty) {
      throw ArgumentError.value(
          newName, 'newName', 'The child name cannot be empty.');
    }

    final profile = StudentProfile(
      id: profileId,
      name: normalizedName,
      dateOfBirth: state.dateOfBirth,
      captionsEnabled: state.captionsEnabled,
      mathScore: state.mathScore,
      scienceScore: state.scienceScore,
      lastActive: DateTime.now(),
    );
    await _database.insertOrUpdateProfile(profile);
    state = state.copyWith(name: normalizedName, clearError: true);
  }

  Future<void> updateDateOfBirth(DateTime dateOfBirth) async {
    final normalizedDate = DateTime(
      dateOfBirth.year,
      dateOfBirth.month,
      dateOfBirth.day,
    );
    final today = DateTime.now();
    final normalizedToday = DateTime(today.year, today.month, today.day);
    if (normalizedDate.isAfter(normalizedToday)) {
      throw ArgumentError.value(
        dateOfBirth,
        'dateOfBirth',
        'The date of birth cannot be in the future.',
      );
    }

    await _database.insertOrUpdateProfile(
      StudentProfile(
        id: profileId,
        name: state.name,
        dateOfBirth: normalizedDate,
        captionsEnabled: state.captionsEnabled,
        mathScore: state.mathScore,
        scienceScore: state.scienceScore,
        lastActive: DateTime.now(),
      ),
    );
    state = state.copyWith(
      dateOfBirth: normalizedDate,
      clearError: true,
    );
  }

  Future<void> updateCaptionsEnabled(bool enabled) async {
    await _database.insertOrUpdateProfile(
      StudentProfile(
        id: profileId,
        name: state.name,
        dateOfBirth: state.dateOfBirth,
        captionsEnabled: enabled,
        mathScore: state.mathScore,
        scienceScore: state.scienceScore,
        lastActive: DateTime.now(),
      ),
    );
    state = state.copyWith(
      captionsEnabled: enabled,
      clearError: true,
    );
  }

  Future<void> recordLesson(LearningDomain domain) async {
    final nextMathScore =
        domain == LearningDomain.math ? state.mathScore + 1 : state.mathScore;
    final nextScienceScore = domain == LearningDomain.science
        ? state.scienceScore + 1
        : state.scienceScore;

    final profile = StudentProfile(
      id: profileId,
      name: state.name,
      dateOfBirth: state.dateOfBirth,
      captionsEnabled: state.captionsEnabled,
      mathScore: nextMathScore,
      scienceScore: nextScienceScore,
      lastActive: DateTime.now(),
    );
    await _database.insertOrUpdateProfile(profile);
    state = state.copyWith(
      mathScore: nextMathScore,
      scienceScore: nextScienceScore,
      clearError: true,
    );
  }

  Future<void> incrementMathScore() => recordLesson(LearningDomain.math);

  Future<void> incrementScienceScore() => recordLesson(LearningDomain.science);

  Future<void> clearConversationHistory() =>
      _database.clearConversationHistory();

  StudentProfile _defaultProfile() {
    return StudentProfile(
      id: profileId,
      name: 'Tiny Explorer',
      dateOfBirth: null,
      captionsEnabled: false,
      mathScore: 0,
      scienceScore: 0,
      lastActive: DateTime.now(),
    );
  }
}

final studentProvider =
    StateNotifierProvider<StudentStateNotifier, StudentState>((ref) {
  return StudentStateNotifier(ref.watch(databaseProvider));
});

final avaRealtimeAgeBandProvider = Provider<AvaRealtimeAgeBand>((ref) {
  return deriveAvaRealtimeAgeBand(
    ref.watch(studentProvider).dateOfBirth,
  );
});

final avaSessionProvider =
    StateNotifierProvider<AvaRealtimeSessionController, AvaSessionState>((ref) {
  return AvaRealtimeSessionController(
    runtimeFactory: ref.watch(avaRealtimeRuntimeFactoryProvider),
    presentation: ref.watch(avaRealtimePresentationProvider.notifier),
    canvasEngine: ref.watch(avaCanvasEngineProvider.notifier),
    ageBand: () => ref.read(avaRealtimeAgeBandProvider),
    volume: () => ref.read(volumeProvider),
  );
});
