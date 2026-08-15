import 'dart:async';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter/services.dart';

import '../../config/ava_assets.dart';
import '../../models/ava_performance_plan.dart';
import '../../models/ava_realtime_protocol.dart';
import '../../models/lesson_response.dart';
import '../../services/ava_realtime_presentation.dart';
import '../../state/app_state.dart';
import '../../state/ava_session_state.dart';
import 'ava_3d_viewer.dart';
import 'ava_filament_motion_driver.dart';
import 'ava_filament_viewer.dart';
import 'avatar_action_intent.dart';
import 'avatar_ambient_motion_controller.dart';
import 'avatar_anatomy.dart';
import 'avatar_animation_coordinator.dart';
import 'avatar_camera_policy.dart';
import 'avatar_performance_controller.dart';
import 'avatar_reaction_director.dart';
import 'avatar_reaction_profiles.dart';
import 'avatar_response_animation_director.dart';
import 'avatar_viewer_controller.dart';

enum AvaRendererBackend { filament, modelViewer }

AvaRendererBackend _configuredRendererBackend() {
  const configured = String.fromEnvironment(
    'AVA_RENDERER',
    defaultValue: 'filament',
  );
  return switch (configured) {
    'filament' => AvaRendererBackend.filament,
    'modelViewer' => AvaRendererBackend.modelViewer,
    _ => throw StateError(
        'Unsupported AVA_RENDERER=$configured. '
        'Use filament or modelViewer.',
      ),
  };
}

class AvatarView extends ConsumerStatefulWidget {
  const AvatarView({super.key});

  @override
  ConsumerState<AvatarView> createState() => _AvatarViewState();
}

class _AvatarViewState extends ConsumerState<AvatarView>
    with SingleTickerProviderStateMixin {
  static const _debugAuditionRequested = bool.fromEnvironment(
    'AVA_PERFORMANCE_AUDITION',
  );
  static const _debugAuditionAutoplayRequested = bool.fromEnvironment(
    'AVA_PERFORMANCE_AUDITION_AUTOPLAY',
  );
  static const _idleAnimation = 'AvaIdle';
  // The VRM eye line is at about Y=1.41 while the full-body composition is
  // centered near the hips. A 77-degree polar orbit places the camera at eye
  // height without aiming the full-body frame at Ava's face and clipping her
  // feet.
  static const _cameraTargetY = 0.90;
  static const _cameraElevation = 77.0;
  static const _cameraDistance = 2.18;
  static const _gestureConfiguration = AvaViewerGestureConfiguration(
    frontPolarAngleDegrees: _cameraElevation,
    minimumPolarAngleDegrees: 66,
    maximumPolarAngleDegrees: 92,
    autoCenterDelay: Duration(seconds: 30),
  );

  late final AnimationController _tapFeedbackController;
  late final AvaRendererBackend _rendererBackend;
  late final AvaAvatarViewerController _modelController;
  late final AvatarAnimationCoordinator _animationCoordinator;
  AvatarPerformanceController? _performanceController;
  AvatarResponseAnimationDirector? _responseAnimationDirector;
  AvatarAmbientMotionController? _ambientMotionController;
  AvatarReactionDirector? _reactionDirector;
  late final Stopwatch _interactionClock;

  Set<String> _availableAnimations = const <String>{};
  AvatarAnatomyMap? _anatomyMap;
  double _loadingProgress = 0;
  String? _modelError;
  Alignment _tapFeedbackAlignment = Alignment.center;
  double _tapFeedbackStrength = 0.72;
  bool _debugAuditionRunning = false;
  int _debugAuditionGeneration = 0;
  Future<void> _realtimeMotionTail = Future<void>.value();
  String? _activeRealtimeMotionTurnId;
  int _lastRealtimeLipSyncRevision = -1;
  String? _canvasAttentionTurnId;

  bool get _debugAuditionEnabled =>
      kDebugMode &&
      _debugAuditionRequested &&
      _rendererBackend == AvaRendererBackend.filament;

  bool get _modelReady =>
      _loadingProgress >= 1 &&
      _modelError == null &&
      _availableAnimations.isNotEmpty &&
      _anatomyMap != null &&
      _reactionDirector != null;

  @override
  void initState() {
    super.initState();
    _rendererBackend = _configuredRendererBackend();
    _modelController = switch (_rendererBackend) {
      AvaRendererBackend.filament => AvaFilamentController(),
      AvaRendererBackend.modelViewer => Ava3DController(),
    };
    _interactionClock = Stopwatch()..start();
    final modelController = _modelController;
    if (modelController is AvaFilamentController) {
      _performanceController = AvatarPerformanceController(
        driver: AvaFilamentMotionDriver(
          sink: modelController,
          clock: () => _interactionClock.elapsed,
        ),
        onFailure: _handleAnimationFailure,
      );
      _responseAnimationDirector = AvatarResponseAnimationDirector(
        now: () => _interactionClock.elapsed,
        dispatch: (intent, now) async {
          final controller = _performanceController;
          if (controller == null) {
            throw StateError('Ava performance controller is unavailable.');
          }
          await controller.dispatch(intent, now: now);
        },
        cancelSource: (source, now) async {
          final controller = _performanceController;
          if (controller == null) {
            throw StateError('Ava performance controller is unavailable.');
          }
          await controller.cancelSource(source, now: now);
        },
      );
      _ambientMotionController = AvatarAmbientMotionController(
        now: () => _interactionClock.elapsed,
        dispatch: (intent, now) async {
          final controller = _performanceController;
          if (controller == null) {
            throw StateError('Ava performance controller is unavailable.');
          }
          await controller.dispatch(intent, now: now);
        },
        onFailure: _handleAnimationFailure,
      );
    }
    _animationCoordinator = AvatarAnimationCoordinator(
      idleAnimation: _idleAnimation,
      // Speaking remains on living idle. Authored response motions are finite
      // manifest clips, never a continuously looping "explain" animation.
      explainingAnimation: _idleAnimation,
      playAnimation: ({
        required animationName,
        required loopCount,
        required playbackRate,
      }) {
        return _modelController.playAnimation(
          animationName: animationName,
          loopCount: loopCount,
          playbackRate: playbackRate,
        );
      },
      onPlaybackFailure: _handleAnimationFailure,
    );
    _tapFeedbackController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 720),
    );
  }

  @override
  void dispose() {
    _debugAuditionGeneration += 1;
    _interactionClock.stop();
    _ambientMotionController?.dispose();
    _ambientMotionController = null;
    _reactionDirector?.reset();
    _animationCoordinator.dispose();
    _tapFeedbackController.dispose();
    final performanceController = _performanceController;
    _performanceController = null;
    if (performanceController != null) {
      unawaited(_disposePerformanceAndModel(performanceController));
    } else {
      _modelController.dispose();
    }
    super.dispose();
  }

  Future<void> _disposePerformanceAndModel(
    AvatarPerformanceController performanceController,
  ) async {
    try {
      await performanceController.dispose();
    } catch (error, stackTrace) {
      FlutterError.reportError(
        FlutterErrorDetails(
          exception: error,
          stack: stackTrace,
          library: 'avatar_performance',
          context: ErrorDescription(
            'while disposing Ava\'s native performance controller',
          ),
        ),
      );
    } finally {
      _modelController.dispose();
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(
      avaSessionProvider.select((state) => state.turnState),
      (previous, next) => _handleSessionPhaseChanged(previous, next),
    );
    ref.listen(
      avaRealtimePresentationProvider.select(
        (controller) => controller.state.revision,
      ),
      (_, __) => _queueRealtimePresentationSync(),
    );

    final legacyExpression = ref.watch(
      avaSessionProvider.select(
        (state) => state.response?.expression ?? AvaExpression.neutral,
      ),
    );
    final legacySessionBusy = ref.watch(
      avaSessionProvider.select((state) => state.isBusy),
    );
    final realtimeState = ref.watch(avaRealtimePresentationProvider).state;
    final expression = realtimeState.isActive
        ? _legacyExpressionFor(realtimeState.currentSegment?.expression)
        : legacyExpression;
    final sessionBusy = legacySessionBusy || realtimeState.isActive;

    final glowColor = switch (expression) {
      AvaExpression.happy ||
      AvaExpression.encouraging =>
        const Color(0xFFFFC6D0),
      AvaExpression.thinking => const Color(0xFFC9DDFB),
      AvaExpression.surprised => const Color(0xFFFFDEAA),
      AvaExpression.neutral => const Color(0xFFD9E7E4),
    };

    return Semantics(
      button: true,
      label: 'Ava, your interactive teacher. Tap Ava for a reaction.',
      child: Stack(
        fit: StackFit.expand,
        children: [
          Center(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 300),
              width: 330,
              height: 430,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(180),
                gradient: RadialGradient(
                  colors: [
                    glowColor.withValues(alpha: 0.12),
                    glowColor.withValues(alpha: 0),
                  ],
                  stops: const [0, 1],
                ),
                boxShadow: [
                  BoxShadow(
                    color: glowColor.withValues(alpha: 0.08),
                    blurRadius: 70,
                    spreadRadius: 10,
                  ),
                ],
              ),
            ),
          ),
          Align(
            alignment: const Alignment(0, 0.9),
            child: Container(
              width: 150,
              height: 12,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(999),
                color: const Color(0xFF44504E).withValues(alpha: 0.07),
                boxShadow: [
                  BoxShadow(
                    color: const Color(0xFF44504E).withValues(alpha: 0.12),
                    blurRadius: 24,
                    spreadRadius: 8,
                  ),
                ],
              ),
            ),
          ),
          _buildAvatarRenderer(),
          IgnorePointer(
            child: AnimatedBuilder(
              animation: _tapFeedbackController,
              builder: (context, child) {
                final progress = Curves.easeOutCubic.transform(
                  _tapFeedbackController.value,
                );
                final opacity = (1 - progress).clamp(0.0, 1.0);

                return Stack(
                  fit: StackFit.expand,
                  children: [
                    Align(
                      alignment: _tapFeedbackAlignment,
                      child: Opacity(
                        opacity: opacity * 0.55,
                        child: Transform.scale(
                          scale: _tapFeedbackStrength + progress * 0.48,
                          child: Container(
                            width: 82,
                            height: 82,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: const Color(0xFFE88999),
                                width: 2,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                    Align(
                      alignment: _tapFeedbackAlignment,
                      child: Opacity(
                        opacity: opacity,
                        child: Transform.translate(
                          offset: Offset(0, -22 * progress),
                          child: const Icon(
                            Icons.auto_awesome_rounded,
                            size: 20,
                            color: Color(0xFFE88999),
                          ),
                        ),
                      ),
                    ),
                  ],
                );
              },
            ),
          ),
          if (_modelError case final error?)
            Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Text(
                  error,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    color: Color(0xFF8C2634),
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            )
          else if (_loadingProgress < 1)
            Center(
              child: SizedBox(
                width: 30,
                height: 30,
                child: CircularProgressIndicator(
                  value: _loadingProgress == 0 ? null : _loadingProgress,
                  strokeWidth: 2.5,
                  color: const Color(0xFFDA7285),
                ),
              ),
            ),
          if (_debugAuditionEnabled)
            SafeArea(
              child: Align(
                alignment: Alignment.topLeft,
                child: Padding(
                  padding: const EdgeInsets.only(top: 8, left: 12),
                  child: _PerformanceAuditionButton(
                    running: _debugAuditionRunning,
                    onPressed: !_modelReady || sessionBusy
                        ? null
                        : () => unawaited(_runDebugPerformanceAudition()),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildAvatarRenderer() {
    void onProgress(double progress) {
      if (!mounted) return;
      setState(() => _loadingProgress = progress);
    }

    void onError(String error) {
      if (!mounted) return;
      _animationCoordinator.markError();
      setState(() {
        _modelError = 'Could not load Ava\'s 3D model: $error';
      });
    }

    return switch (_rendererBackend) {
      AvaRendererBackend.filament => AvaFilamentViewer(
          controller: _modelController as AvaFilamentController,
          src: AvaAssets.avatarModel,
          anatomyMapAsset: AvaAssets.avatarTouchMap,
          rigBindingsAsset: AvaAssets.avatarRigBindings,
          gestureConfiguration: _gestureConfiguration,
          onAnatomicalTap: _handleFilamentAnatomicalTap,
          onAnimationFinished: _handleAnimationFinished,
          onProfileReady: (profile) {
            assert(() {
              debugPrint(
                'AVA_FILAMENT_PROFILE bones=${profile.boneNames.length} '
                'morphs=${profile.morphTargetNames.length} '
                'animations=${profile.animationNames.length}',
              );
              return true;
            }());
          },
          onProgress: onProgress,
          onLoad: (_) => _initializeLoadedModel(),
          onError: onError,
        ),
      AvaRendererBackend.modelViewer => Ava3DViewer(
          controller: _modelController as Ava3DController,
          src: AvaAssets.avatarModel,
          gestureConfiguration: _gestureConfiguration,
          onSurfaceTap: _handleViewerSurfaceTap,
          onAnimationFinished: _handleAnimationFinished,
          progressBarColor: Colors.transparent,
          onProgress: onProgress,
          onLoad: (_) => _initializeLoadedModel(),
          onError: onError,
        ),
    };
  }

  void _handleAnimationFinished(AvaAnimationFinishedEvent event) {
    try {
      final accepted = _animationCoordinator.handleAnimationFinished(
        playbackId: event.playbackId,
        animationName: event.animationName,
      );
      assert(() {
        debugPrint(
          'AVA_ANIMATION_FINISHED id=${event.playbackId} '
          'animation=${event.animationName} accepted=$accepted',
        );
        return true;
      }());
    } catch (error, stackTrace) {
      _handleAnimationFailure(error, stackTrace);
    }
  }

  void _handleAnimationFailure(Object error, StackTrace stackTrace) {
    if (!mounted) return;
    FlutterError.reportError(
      FlutterErrorDetails(
        exception: error,
        stack: stackTrace,
        library: 'avatar_animation',
        context: ErrorDescription('while playing an Ava animation'),
      ),
    );
    _animationCoordinator.markError();
    setState(() {
      _modelError = 'Ava could not complete her animation: $error';
    });
  }

  Future<void> _initializeLoadedModel() async {
    try {
      final animationsFuture = _modelController.getAvailableAnimations();
      final anatomySourceFuture = rootBundle.loadString(
        AvaAssets.avatarTouchMap,
      );
      final reactionProfilesSourceFuture = rootBundle.loadString(
        AvaAssets.avatarReactionProfiles,
      );
      final modelBytesFuture = rootBundle.load(AvaAssets.avatarModel);
      final animations = (await animationsFuture).toSet();
      final anatomyMap = AvatarAnatomyMap.fromJsonString(
        await anatomySourceFuture,
      );
      final reactionProfiles =
          AvatarReactionMotionProfileCatalog.fromJsonString(
        await reactionProfilesSourceFuture,
      );
      final modelData = await modelBytesFuture;
      final modelBytes = modelData.buffer.asUint8List(
        modelData.offsetInBytes,
        modelData.lengthInBytes,
      );
      final modelSha256 = await compute(_sha256Hex, modelBytes);
      if (modelSha256 != anatomyMap.modelSha256) {
        throw StateError(
          'Ava\'s anatomical touch map does not match the loaded 3D model.',
        );
      }
      final requiredAnimations = <String>{
        _idleAnimation,
        ...AvatarReactionCatalog.requiredAnimationNames,
      };
      final missingAnimations = requiredAnimations.difference(animations);
      if (missingAnimations.isNotEmpty) {
        throw StateError(
          'Ava is missing required animation clips: ${missingAnimations.join(', ')}.',
        );
      }

      _modelController.setCameraTarget(0, _cameraTargetY, 0);
      _modelController.setFrontCameraOrbit(
        0,
        _cameraElevation,
        _cameraDistance,
      );
      _animationCoordinator.setBaseAnimation(
        _baseAnimationFor(ref.read(avaSessionProvider).turnState),
      );
      _animationCoordinator.markReady();
      if (_modelController is AvaFilamentController) {
        final performanceController = _performanceController;
        if (performanceController == null) {
          throw StateError('Ava native performance driver was not created.');
        }
        await performanceController.initialize();
        await (_modelController as AvaFilamentController).startLivingIdle();
        final ambientMotionController = _ambientMotionController;
        if (ambientMotionController == null) {
          throw StateError('Ava ambient motion controller was not created.');
        }
        ambientMotionController.start();
      }
      assert(() {
        debugPrint(
          'AVA_MODEL_READY animations=${animations.toList()..sort()}',
        );
        return true;
      }());

      if (mounted) {
        setState(() {
          _availableAnimations = animations;
          _anatomyMap = anatomyMap;
          _reactionDirector = AvatarReactionDirector(
            profiles: reactionProfiles,
          );
          _loadingProgress = 1;
          _modelError = null;
        });
        if (_debugAuditionEnabled && _debugAuditionAutoplayRequested) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (!mounted || _debugAuditionRunning) return;
            unawaited(_runDebugPerformanceAudition());
          });
        }
        _queueRealtimePresentationSync();
      }
    } catch (error) {
      if (!mounted) return;
      _animationCoordinator.markError();
      setState(() {
        _modelError = 'Ava loaded, but her interaction setup failed: $error';
      });
    }
  }

  void _handleViewerSurfaceTap(AvaViewerPointerEvent event) {
    if (!_modelReady) {
      return;
    }
    try {
      final anatomyMap = _anatomyMap;
      if (anatomyMap == null) {
        throw StateError('Ava\'s anatomical touch map is not loaded.');
      }
      final anatomy = anatomyMap.contactFor(event.surface);
      _handleResolvedAnatomicalTap(
        anatomy: anatomy,
        position: event.position,
        viewportSize: event.viewportSize,
        modelPosition: event.modelPosition,
        modelNormal: event.modelNormal,
        contactDuration: event.contactDuration,
        peakPressure: event.peakPressure,
        pointerType: event.pointerType,
        cameraAzimuth: event.cameraAzimuth,
        cameraElevation: event.cameraElevation,
        avatarRootYaw: event.avatarRootYaw,
      );
    } catch (error, stackTrace) {
      FlutterError.reportError(
        FlutterErrorDetails(
          exception: error,
          stack: stackTrace,
          library: 'avatar_interaction',
          context: ErrorDescription(
            'while resolving and reacting to an Ava surface touch',
          ),
        ),
      );
      _animationCoordinator.markError();
      setState(() {
        _modelError = 'Ava could not process her touch reaction: $error';
      });
    }
  }

  void _handleFilamentAnatomicalTap(AvaFilamentPointerEvent event) {
    if (!_modelReady) return;
    _handleResolvedAnatomicalTap(
      anatomy: event.anatomy,
      position: event.position,
      viewportSize: event.viewportSize,
      modelPosition: event.modelPosition,
      modelNormal: event.modelNormal,
      contactDuration: event.contactDuration,
      peakPressure: event.peakPressure,
      pointerType: event.pointerType,
      cameraAzimuth: event.cameraAzimuth,
      cameraElevation: event.cameraElevation,
      avatarRootYaw: event.avatarRootYaw,
    );
  }

  void _handleResolvedAnatomicalTap({
    required AvatarAnatomicalContact anatomy,
    required Offset position,
    required Size viewportSize,
    required AvatarModelVector3 modelPosition,
    required AvatarModelVector3 modelNormal,
    required Duration contactDuration,
    required double peakPressure,
    required String pointerType,
    required double cameraAzimuth,
    required double cameraElevation,
    required double avatarRootYaw,
  }) {
    try {
      final session = ref.read(avaSessionProvider);
      final realtime = ref.read(avaRealtimePresentationProvider).state;
      final reactionDirector = _reactionDirector;
      if (reactionDirector == null) {
        throw StateError('Ava\'s reaction profiles are not loaded.');
      }
      final decision = reactionDirector.decide(
        contact: AvatarTouchContact(
          anatomy: anatomy,
          modelPosition: modelPosition,
          modelNormal: modelNormal,
          spatialContext: AvatarSpatialContext(
            cameraAzimuth: cameraAzimuth,
            cameraElevation: cameraElevation,
            avatarRootYaw: avatarRootYaw,
          ),
          duration: contactDuration,
          peakPressure: peakPressure,
          pointerType: pointerType,
          occurredAt: _interactionClock.elapsed,
        ),
        phase: realtime.isActive
            ? _legacyPhaseFor(realtime.phase)
            : session.turnState,
        expression: realtime.isActive
            ? _legacyExpressionFor(realtime.currentSegment?.expression)
            : session.response?.expression ?? AvaExpression.neutral,
      );
      final normalizedX = (position.dx / viewportSize.width).clamp(0.0, 1.0);
      final normalizedY = (position.dy / viewportSize.height).clamp(0.0, 1.0);
      setState(() {
        _tapFeedbackAlignment = Alignment(
          normalizedX * 2 - 1,
          normalizedY * 2 - 1,
        );
        _tapFeedbackStrength = decision.feedbackStrength;
      });
      _tapFeedbackController.forward(from: 0);

      final reaction = decision.animation;
      final proceduralTouchIntent = _proceduralTouchIntent(decision.intents);
      final filamentController = _modelController is AvaFilamentController
          ? _modelController as AvaFilamentController
          : null;
      final performanceController = _performanceController;
      final usesProceduralTouch = reaction != null &&
          proceduralTouchIntent != null &&
          filamentController != null &&
          performanceController?.isReady == true;
      if (usesProceduralTouch) {
        _animationCoordinator.cancelReactionAndResumeBase();
        unawaited(
          performanceController!
              .dispatch(
            proceduralTouchIntent,
            now: proceduralTouchIntent.occurredAt,
          )
              .then<void>(
            (_) {},
            onError: (Object error, StackTrace stackTrace) {
              _handleAnimationFailure(error, stackTrace);
            },
          ),
        );
      } else if (reaction != null) {
        if (filamentController == null) {
          _animationCoordinator.playReaction(reaction);
        } else {
          final clearTouchMotion = performanceController == null
              ? filamentController.clearProceduralMotion()
              : performanceController
                  .cancelSource(
                    AvatarActionSource.touch,
                    now: _interactionClock.elapsed,
                  )
                  .then<void>((_) {});
          unawaited(clearTouchMotion.then<void>(
            (_) {
              if (!mounted) return;
              _animationCoordinator.playReaction(reaction);
            },
            onError: (Object error, StackTrace stackTrace) {
              _handleAnimationFailure(error, stackTrace);
            },
          ));
        }
      }
      assert(() {
        debugPrint(
          'AVA_SURFACE_TOUCH renderer=${_rendererBackend.name} '
          'region=${anatomy.region.name} family=${decision.family} '
          'intensity=${decision.intensity} stage=${decision.stage.name} '
          'streak=${decision.streak} '
          'recentRegions=${decision.distinctRecentRegions} '
          'bone=${anatomy.primaryBone.jointName} '
          'u=${anatomy.longitudinalPosition.toStringAsFixed(3)} '
          'radial=${anatomy.radialAngle.toStringAsFixed(3)} '
          'surface=${anatomy.surfaceSector.name} '
          'view=${cameraAzimuth.toStringAsFixed(3)} '
          'intents=${decision.intents.map((intent) => intent.kind.name).join(',')} '
          'animation=${usesProceduralTouch ? 'proceduralTouch' : reaction?.name ?? 'coalesced'}',
        );
        return true;
      }());
    } catch (error, stackTrace) {
      FlutterError.reportError(
        FlutterErrorDetails(
          exception: error,
          stack: stackTrace,
          library: 'avatar_interaction',
          context: ErrorDescription(
            'while directing an Ava anatomical touch reaction',
          ),
        ),
      );
      _animationCoordinator.markError();
      setState(() {
        _modelError = 'Ava could not process her touch reaction: $error';
      });
    }
  }

  void _handleSessionPhaseChanged(
    AvaSessionPhase? previous,
    AvaSessionPhase next,
  ) {
    if (!_modelReady) {
      return;
    }

    if (AvatarCameraPolicy.shouldRecenterForConversation(previous, next)) {
      try {
        _modelController.recenterCamera();
      } catch (error, stackTrace) {
        FlutterError.reportError(
          FlutterErrorDetails(
            exception: error,
            stack: stackTrace,
            library: 'avatar_camera',
            context: ErrorDescription(
              'while returning Ava to her front-facing position',
            ),
          ),
        );
        setState(() {
          _modelError = 'Ava could not face forward: $error';
        });
        return;
      }
    }

    final wasSpeaking = previous == AvaSessionPhase.speaking;
    final isSpeaking = next == AvaSessionPhase.speaking;
    if (wasSpeaking == isSpeaking) {
      return;
    }

    _animationCoordinator.setBaseAnimation(
      _baseAnimationFor(next),
    );
  }

  // Speaking stays on the approved living idle until an authored one-shot
  // motion library replaces the rejected continuously looping explain clip.
  AvatarBaseAnimation _baseAnimationFor(AvaSessionPhase _) =>
      AvatarBaseAnimation.idle;

  void _queueRealtimePresentationSync() {
    final state = ref.read(avaRealtimePresentationProvider).state;
    _realtimeMotionTail = _realtimeMotionTail
        .then((_) => _synchronizeRealtimePresentation(state))
        .catchError((Object error, StackTrace stackTrace) {
      _handleAnimationFailure(error, stackTrace);
    });
  }

  Future<void> _synchronizeRealtimePresentation(
    AvaRealtimePresentationState state,
  ) async {
    if (!mounted || !_modelReady) return;
    final director = _responseAnimationDirector;
    if (director == null) return;

    if (state.isActive && state.turnId != _activeRealtimeMotionTurnId) {
      if (director.isActive) {
        await director.cancelTurn();
      }
      _modelController.recenterCamera();
      director.beginTurn(state.turnId!);
      _activeRealtimeMotionTurnId = state.turnId;
      _lastRealtimeLipSyncRevision = -1;
      _canvasAttentionTurnId = null;
    }

    if (state.isActive &&
        state.canvasApplied &&
        state.turnId != _canvasAttentionTurnId) {
      await director.presentValidatedCanvas();
      _canvasAttentionTurnId = state.turnId;
    }

    final timeline = state.lipSync;
    if (state.isActive &&
        timeline != null &&
        state.revision != _lastRealtimeLipSyncRevision &&
        timeline.renderedSampleCount < timeline.durationSampleCount) {
      await director.synchronizeLipSync(
        AvatarLipSyncTarget(
          audioDuration: _sampleDuration(
            timeline.durationSampleCount,
            timeline.sampleRateHz,
          ),
          audioPosition: _sampleDuration(
            timeline.renderedSampleCount,
            timeline.sampleRateHz,
          ),
          cues: [
            for (final cue in timeline.cues)
              AvatarVisemeCue(
                viseme: _avatarViseme(cue.viseme),
                start: _sampleDuration(
                  cue.sampleStart,
                  timeline.sampleRateHz,
                ),
                end: _sampleDuration(
                  cue.sampleEnd,
                  timeline.sampleRateHz,
                ),
                weight: cue.weight,
              ),
          ],
        ),
      );
      _lastRealtimeLipSyncRevision = state.revision;
    }

    if (!state.isActive &&
        state.turnId == _activeRealtimeMotionTurnId &&
        director.isActive) {
      if (state.phase == AvaRealtimeTurnPhase.completed) {
        await director.finishTurn();
      } else {
        await director.cancelTurn();
      }
      _activeRealtimeMotionTurnId = null;
      _lastRealtimeLipSyncRevision = -1;
      _canvasAttentionTurnId = null;
    }
  }

  Duration _sampleDuration(int samples, int sampleRateHz) => Duration(
        microseconds:
            (samples * Duration.microsecondsPerSecond) ~/ sampleRateHz,
      );

  AvatarViseme _avatarViseme(AvaRealtimeViseme viseme) => switch (viseme) {
        AvaRealtimeViseme.aa => AvatarViseme.aa,
        AvaRealtimeViseme.ih => AvatarViseme.ih,
        AvaRealtimeViseme.ou => AvatarViseme.ou,
        AvaRealtimeViseme.ee => AvatarViseme.ee,
        AvaRealtimeViseme.oh => AvatarViseme.oh,
        AvaRealtimeViseme.closed => AvatarViseme.closed,
      };

  AvaExpression _legacyExpressionFor(AvaRealtimeExpression? expression) =>
      switch (expression) {
        AvaRealtimeExpression.warmSmile ||
        AvaRealtimeExpression.encouraging =>
          AvaExpression.encouraging,
        AvaRealtimeExpression.curious => AvaExpression.happy,
        AvaRealtimeExpression.thinking => AvaExpression.thinking,
        AvaRealtimeExpression.neutral ||
        AvaRealtimeExpression.concerned ||
        AvaRealtimeExpression.serious ||
        null =>
          AvaExpression.neutral,
      };

  AvaSessionPhase _legacyPhaseFor(AvaRealtimeTurnPhase? phase) =>
      switch (phase) {
        AvaRealtimeTurnPhase.listening => AvaSessionPhase.listening,
        AvaRealtimeTurnPhase.planning ||
        AvaRealtimeTurnPhase.connecting ||
        AvaRealtimeTurnPhase.created ||
        AvaRealtimeTurnPhase.synthesizing =>
          AvaSessionPhase.thinking,
        AvaRealtimeTurnPhase.speaking => AvaSessionPhase.speaking,
        AvaRealtimeTurnPhase.completed ||
        AvaRealtimeTurnPhase.cancelled ||
        AvaRealtimeTurnPhase.failed ||
        AvaRealtimeTurnPhase.cancelling ||
        null =>
          AvaSessionPhase.idle,
      };

  AvatarActionIntent? _proceduralTouchIntent(
    List<AvatarActionIntent> intents,
  ) {
    for (final intent in intents) {
      final target = intent.target;
      if (target is! AvatarContactTarget) continue;
      final isPrimaryTouchAction = switch (intent.kind) {
        AvatarActionKind.recoil ||
        AvatarActionKind.weightShift ||
        AvatarActionKind.handPose ||
        AvatarActionKind.protectiveRedirect =>
          true,
        _ => false,
      };
      if (isPrimaryTouchAction) {
        return intent;
      }
    }
    return null;
  }

  Future<void> _runDebugPerformanceAudition() async {
    if (!_debugAuditionEnabled) {
      throw UnsupportedError(
        'The response audition requires a debug build with '
        'AVA_PERFORMANCE_AUDITION=true.',
      );
    }
    if (_debugAuditionRunning) {
      throw StateError('Ava is already playing the response audition.');
    }
    if (!_modelReady) {
      throw StateError('Ava must finish loading before the audition starts.');
    }
    if (ref.read(avaSessionProvider).isBusy) {
      throw StateError(
        'A live conversation is active; stop it before the audition.',
      );
    }
    final performanceController = _performanceController;
    final director = _responseAnimationDirector;
    if (performanceController?.isReady != true || director == null) {
      throw StateError(
        'Ava\'s production response-animation pipeline is not ready.',
      );
    }

    final generation = ++_debugAuditionGeneration;
    setState(() => _debugAuditionRunning = true);
    try {
      assert(() {
        debugPrint('AVA_RESPONSE_AUDITION started generation=$generation');
        return true;
      }());
      _modelController.recenterCamera();
      _animationCoordinator.setBaseAnimation(AvatarBaseAnimation.idle);
      director.beginTurn('audition_$generation');
      for (final segment in _debugAuditionSegments) {
        _ensureDebugAuditionActive(generation);
        assert(() {
          debugPrint(
            'AVA_RESPONSE_AUDITION segment=${segment.segment.id} '
            'motion=livingIdle+lipSync '
            'durationMs=${segment.duration.inMilliseconds}',
          );
          return true;
        }());
        await director.startSegment(
          segment.segment,
          expectedDuration: segment.duration,
        );
        await _playDebugSpeechTimeline(
          director: director,
          segment: segment,
          generation: generation,
        );
      }
      _ensureDebugAuditionActive(generation);
      await director.finishTurn();
      assert(() {
        debugPrint('AVA_RESPONSE_AUDITION completed generation=$generation');
        return true;
      }());
    } on _DebugAuditionCancelled {
      return;
    } catch (error, stackTrace) {
      if (mounted) {
        _handleAnimationFailure(error, stackTrace);
      }
    } finally {
      if (mounted && generation == _debugAuditionGeneration) {
        _animationCoordinator.setBaseAnimation(
          _baseAnimationFor(ref.read(avaSessionProvider).turnState),
        );
        setState(() => _debugAuditionRunning = false);
      }
    }
  }

  Future<void> _playDebugSpeechTimeline({
    required AvatarResponseAnimationDirector director,
    required _DebugAuditionSegment segment,
    required int generation,
  }) async {
    final cues = <AvatarVisemeCue>[
      for (var index = 0; index < segment.visemes.length; index += 1)
        AvatarVisemeCue(
          viseme: segment.visemes[index].viseme,
          start: segment.visemes[index].at,
          end: index + 1 < segment.visemes.length
              ? segment.visemes[index + 1].at
              : segment.duration,
          weight: segment.visemes[index].weight,
        ),
    ];
    await director.synchronizeLipSync(
      AvatarLipSyncTarget(
        audioDuration: segment.duration,
        cues: cues,
      ),
    );
    final sampleClock = Stopwatch()..start();
    final remaining = segment.duration - sampleClock.elapsed;
    if (remaining > Duration.zero) {
      await Future<void>.delayed(remaining);
    }
    _ensureDebugAuditionActive(generation);
  }

  void _ensureDebugAuditionActive(int generation) {
    if (!mounted || generation != _debugAuditionGeneration) {
      throw const _DebugAuditionCancelled();
    }
  }
}

class _PerformanceAuditionButton extends StatelessWidget {
  const _PerformanceAuditionButton({
    required this.running,
    required this.onPressed,
  });

  final bool running;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: running
          ? 'Ava response audition is playing'
          : 'Test Ava model response animation',
      child: Material(
        color: const Color(0xFF344342).withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(999),
        child: InkWell(
          key: const ValueKey('avatar-performance-audition'),
          onTap: running ? null : onPressed,
          borderRadius: BorderRadius.circular(999),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (running)
                  const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                else
                  const Icon(
                    Icons.play_arrow_rounded,
                    size: 17,
                    color: Colors.white,
                  ),
                const SizedBox(width: 5),
                Text(
                  running ? 'Playing response' : 'Test response',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _DebugAuditionSegment {
  const _DebugAuditionSegment({
    required this.segment,
    required this.duration,
    required this.visemes,
  });

  final AvaPerformanceSegment segment;
  final Duration duration;
  final List<_DebugVisemeCue> visemes;
}

class _DebugVisemeCue {
  const _DebugVisemeCue({
    required this.at,
    required this.viseme,
  });

  final Duration at;
  final AvatarViseme viseme;
  final double weight = 0.78;
}

class _DebugAuditionCancelled implements Exception {
  const _DebugAuditionCancelled();
}

const _debugAuditionSegments = <_DebugAuditionSegment>[
  _DebugAuditionSegment(
    segment: AvaPerformanceSegment(
      id: 'audition_intro',
      speech: 'Let us look at this together.',
      delivery: AvaDelivery.warm,
      expression: AvaExpressionIntent.warmSmile,
      gaze: AvaGazeIntent.child,
      gesture: AvaGestureIntent.none,
      diagramCue: null,
    ),
    duration: Duration(milliseconds: 1900),
    visemes: [
      _DebugVisemeCue(at: Duration.zero, viseme: AvatarViseme.closed),
      _DebugVisemeCue(at: Duration(milliseconds: 120), viseme: AvatarViseme.aa),
      _DebugVisemeCue(at: Duration(milliseconds: 270), viseme: AvatarViseme.ee),
      _DebugVisemeCue(at: Duration(milliseconds: 430), viseme: AvatarViseme.ou),
      _DebugVisemeCue(at: Duration(milliseconds: 610), viseme: AvatarViseme.ih),
      _DebugVisemeCue(
          at: Duration(milliseconds: 790), viseme: AvatarViseme.closed),
      _DebugVisemeCue(at: Duration(milliseconds: 910), viseme: AvatarViseme.oh),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1080), viseme: AvatarViseme.aa),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1250), viseme: AvatarViseme.ee),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1430), viseme: AvatarViseme.ou),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1630), viseme: AvatarViseme.closed),
    ],
  ),
  _DebugAuditionSegment(
    segment: AvaPerformanceSegment(
      id: 'audition_explanation',
      speech: 'One half means one of two equal parts.',
      delivery: AvaDelivery.encouraging,
      expression: AvaExpressionIntent.encouraging,
      gaze: AvaGazeIntent.child,
      gesture: AvaGestureIntent.none,
      diagramCue: null,
    ),
    duration: Duration(milliseconds: 2700),
    visemes: [
      _DebugVisemeCue(at: Duration.zero, viseme: AvatarViseme.oh),
      _DebugVisemeCue(
          at: Duration(milliseconds: 150), viseme: AvatarViseme.closed),
      _DebugVisemeCue(at: Duration(milliseconds: 280), viseme: AvatarViseme.aa),
      _DebugVisemeCue(at: Duration(milliseconds: 440), viseme: AvatarViseme.ee),
      _DebugVisemeCue(
          at: Duration(milliseconds: 610), viseme: AvatarViseme.closed),
      _DebugVisemeCue(at: Duration(milliseconds: 760), viseme: AvatarViseme.ee),
      _DebugVisemeCue(at: Duration(milliseconds: 920), viseme: AvatarViseme.aa),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1090), viseme: AvatarViseme.closed),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1240), viseme: AvatarViseme.ou),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1410), viseme: AvatarViseme.ih),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1580), viseme: AvatarViseme.oh),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1750), viseme: AvatarViseme.closed),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1900), viseme: AvatarViseme.aa),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2070), viseme: AvatarViseme.ee),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2240), viseme: AvatarViseme.ou),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2430), viseme: AvatarViseme.closed),
    ],
  ),
  _DebugAuditionSegment(
    segment: AvaPerformanceSegment(
      id: 'audition_comparison',
      speech: 'This side is one idea, and this side is the other.',
      delivery: AvaDelivery.warm,
      expression: AvaExpressionIntent.curious,
      gaze: AvaGazeIntent.child,
      gesture: AvaGestureIntent.none,
      diagramCue: null,
    ),
    duration: Duration(milliseconds: 3100),
    visemes: [
      _DebugVisemeCue(at: Duration.zero, viseme: AvatarViseme.closed),
      _DebugVisemeCue(at: Duration(milliseconds: 150), viseme: AvatarViseme.ih),
      _DebugVisemeCue(at: Duration(milliseconds: 320), viseme: AvatarViseme.ee),
      _DebugVisemeCue(
          at: Duration(milliseconds: 500), viseme: AvatarViseme.closed),
      _DebugVisemeCue(at: Duration(milliseconds: 670), viseme: AvatarViseme.aa),
      _DebugVisemeCue(at: Duration(milliseconds: 850), viseme: AvatarViseme.oh),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1030), viseme: AvatarViseme.closed),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1210), viseme: AvatarViseme.ou),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1390), viseme: AvatarViseme.ee),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1570), viseme: AvatarViseme.closed),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1750), viseme: AvatarViseme.ih),
      _DebugVisemeCue(
          at: Duration(milliseconds: 1930), viseme: AvatarViseme.aa),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2110), viseme: AvatarViseme.ou),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2290), viseme: AvatarViseme.closed),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2470), viseme: AvatarViseme.ee),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2650), viseme: AvatarViseme.oh),
      _DebugVisemeCue(
          at: Duration(milliseconds: 2850), viseme: AvatarViseme.closed),
    ],
  ),
];

String _sha256Hex(Uint8List bytes) => sha256.convert(bytes).toString();
