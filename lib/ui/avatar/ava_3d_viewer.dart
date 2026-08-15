// The package's public viewer does not expose its WebView controller, which is
// required for reliable native pointer and animation-finished events. Keep the
// implementation imports isolated in this adapter.
// ignore_for_file: implementation_imports

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_3d_controller/flutter_3d_controller.dart';
import 'package:flutter_3d_controller/src/core/modules/model_viewer/model_viewer.dart';
import 'package:flutter_3d_controller/src/data/datasources/i_flutter_3d_datasource.dart';
import 'package:flutter_3d_controller/src/data/repositories/flutter_3d_repository.dart';
import 'package:flutter_3d_controller/src/utils/utils.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import 'avatar_anatomy.dart';
import 'avatar_viewer_controller.dart';

class AvaViewerGestureConfiguration {
  const AvaViewerGestureConfiguration({
    this.tapSlop = 10,
    this.maximumTapDuration = const Duration(milliseconds: 350),
    this.autoCenterDelay = const Duration(seconds: 30),
    this.orbitSensitivity = 1,
    this.minimumPolarAngleDegrees = 68,
    this.maximumPolarAngleDegrees = 96,
    this.frontPolarAngleDegrees = 82,
    this.interpolationDecay = 120,
  })  : assert(tapSlop > 0),
        assert(orbitSensitivity > 0),
        assert(minimumPolarAngleDegrees >= 0),
        assert(maximumPolarAngleDegrees <= 180),
        assert(minimumPolarAngleDegrees < maximumPolarAngleDegrees),
        assert(frontPolarAngleDegrees >= minimumPolarAngleDegrees),
        assert(frontPolarAngleDegrees <= maximumPolarAngleDegrees),
        assert(interpolationDecay > 0);

  final double tapSlop;
  final Duration maximumTapDuration;
  final Duration autoCenterDelay;
  final int orbitSensitivity;
  final double minimumPolarAngleDegrees;
  final double maximumPolarAngleDegrees;
  final double frontPolarAngleDegrees;
  final double interpolationDecay;
}

class AvaViewerPointerEvent {
  const AvaViewerPointerEvent({
    required this.position,
    required this.viewportSize,
    required this.modelPosition,
    required this.modelNormal,
    required this.surface,
    required this.contactDuration,
    required this.peakPressure,
    required this.pointerType,
    required this.cameraAzimuth,
    required this.cameraElevation,
    required this.cameraRadius,
    required this.avatarRootYaw,
  });

  factory AvaViewerPointerEvent.fromChannelArguments(List<dynamic> arguments) {
    if (arguments.length != 18) {
      throw FormatException(
        'Expected screen position, viewport, model contact, surface, and '
        'pointer and camera measurements; '
        'received ${arguments.length} values.',
      );
    }

    final x = _finiteDouble(arguments[0], 'x');
    final y = _finiteDouble(arguments[1], 'y');
    final width = _finiteDouble(arguments[2], 'viewport width');
    final height = _finiteDouble(arguments[3], 'viewport height');
    if (width <= 0 || height <= 0) {
      throw FormatException(
        'Viewport dimensions must be greater than zero; '
        'received $width x $height.',
      );
    }
    final modelPosition = AvatarModelVector3(
      _finiteDouble(arguments[4], 'model x'),
      _finiteDouble(arguments[5], 'model y'),
      _finiteDouble(arguments[6], 'model z'),
    );
    final modelNormal = AvatarModelVector3(
      _finiteDouble(arguments[7], 'normal x'),
      _finiteDouble(arguments[8], 'normal y'),
      _finiteDouble(arguments[9], 'normal z'),
    );
    final rawSurface = arguments[10]?.toString() ?? '';
    final surface = AvatarSurfaceReference.parse(rawSurface);
    final durationMilliseconds = _finiteDouble(
      arguments[11],
      'contact duration',
    );
    if (durationMilliseconds < 0) {
      throw const FormatException('Contact duration must not be negative.');
    }
    final peakPressure = _finiteDouble(arguments[12], 'peak pressure');
    if (peakPressure < 0 || peakPressure > 1) {
      throw FormatException(
        'Peak pressure must be between zero and one: $peakPressure.',
      );
    }
    final pointerType = arguments[13]?.toString() ?? '';
    if (pointerType.trim().isEmpty) {
      throw const FormatException('Pointer type must not be empty.');
    }
    final cameraAzimuth = _finiteDouble(arguments[14], 'camera azimuth');
    final cameraElevation = _finiteDouble(arguments[15], 'camera elevation');
    final cameraRadius = _finiteDouble(arguments[16], 'camera radius');
    if (cameraRadius <= 0) {
      throw FormatException(
        'Camera radius must be greater than zero: $cameraRadius.',
      );
    }
    final avatarRootYaw = _finiteDouble(arguments[17], 'avatar root yaw');

    return AvaViewerPointerEvent(
      position: Offset(x, y),
      viewportSize: Size(width, height),
      modelPosition: modelPosition,
      modelNormal: modelNormal,
      surface: surface,
      contactDuration: Duration(
        microseconds: (durationMilliseconds * 1000).round(),
      ),
      peakPressure: peakPressure,
      pointerType: pointerType,
      cameraAzimuth: cameraAzimuth,
      cameraElevation: cameraElevation,
      cameraRadius: cameraRadius,
      avatarRootYaw: avatarRootYaw,
    );
  }

  final Offset position;
  final Size viewportSize;
  final AvatarModelVector3 modelPosition;
  final AvatarModelVector3 modelNormal;
  final AvatarSurfaceReference surface;
  final Duration contactDuration;
  final double peakPressure;
  final String pointerType;
  final double cameraAzimuth;
  final double cameraElevation;
  final double cameraRadius;
  final double avatarRootYaw;
}

class AvaAnimationFinishedEvent {
  const AvaAnimationFinishedEvent({
    required this.playbackId,
    required this.animationName,
  });

  factory AvaAnimationFinishedEvent.fromChannelArguments(
    List<dynamic> arguments,
  ) {
    if (arguments.length != 2) {
      throw FormatException(
        'Expected playback id and animation name; '
        'received ${arguments.length} values.',
      );
    }
    final rawPlaybackId = arguments[0];
    final playbackId = rawPlaybackId is num
        ? rawPlaybackId.toInt()
        : int.tryParse(rawPlaybackId?.toString() ?? '');
    final animationName = arguments[1]?.toString() ?? '';
    if (playbackId == null || playbackId <= 0) {
      throw FormatException('Playback id must be a positive integer.');
    }
    if (animationName.isEmpty) {
      throw FormatException('Animation name must not be empty.');
    }
    return AvaAnimationFinishedEvent(
      playbackId: playbackId,
      animationName: animationName,
    );
  }

  final int playbackId;
  final String animationName;
}

double _finiteDouble(Object? value, String fieldName) {
  final parsed = value is num
      ? value.toDouble()
      : double.tryParse(value?.toString() ?? '');
  if (parsed == null || !parsed.isFinite) {
    throw FormatException('$fieldName must be a finite number: $value.');
  }
  return parsed;
}

class Ava3DController implements AvaAvatarViewerController {
  final Flutter3DController _delegate = Flutter3DController();

  InAppWebViewController? _webViewController;
  ValueChanged<String>? _onCommandError;
  String? _viewerId;
  String? _animationFinishedHandlerName;
  ({double theta, double phi, double radius})? _frontCameraOrbit;
  int _nextPlaybackId = 0;
  bool _modelLoaded = false;
  bool _disposed = false;

  void initializeWeb(String viewerId) {
    _ensureNotDisposed();
    _viewerId = viewerId;
    _delegate.init(
      Flutter3DRepository(
        IFlutter3DDatasource(viewerId, null, false),
      ),
    );
  }

  void initializeNative({
    required String viewerId,
    required String animationFinishedHandlerName,
    required InAppWebViewController webViewController,
    required ValueChanged<String> onCommandError,
  }) {
    _ensureNotDisposed();
    _viewerId = viewerId;
    _animationFinishedHandlerName = animationFinishedHandlerName;
    _webViewController = webViewController;
    _onCommandError = onCommandError;
    _delegate.init(
      Flutter3DRepository(
        IFlutter3DDatasource(viewerId, webViewController, false),
      ),
    );
  }

  void markModelLoaded(bool loaded) {
    _ensureNotDisposed();
    _modelLoaded = loaded;
    _delegate.onModelLoaded.value = loaded;
  }

  @override
  Future<List<String>> getAvailableAnimations() {
    _ensureModelReady();
    return _delegate.getAvailableAnimations();
  }

  @override
  void setCameraTarget(double x, double y, double z) {
    _ensureModelReady();
    _delegate.setCameraTarget(x, y, z);
  }

  @override
  void setFrontCameraOrbit(double theta, double phi, double radius) {
    _ensureModelReady();
    _frontCameraOrbit = (theta: theta, phi: phi, radius: radius);
    _delegate.setCameraOrbit(theta, phi, radius);
  }

  @override
  void recenterCamera() {
    _ensureModelReady();
    final frontCameraOrbit = _frontCameraOrbit;
    if (frontCameraOrbit == null) {
      throw StateError(
        'Ava\'s front-facing camera orbit has not been configured.',
      );
    }

    if (kIsWeb) {
      _delegate.setCameraOrbit(
        frontCameraOrbit.theta,
        frontCameraOrbit.phi,
        frontCameraOrbit.radius,
      );
      return;
    }

    final webViewController = _webViewController;
    if (webViewController == null) {
      throw StateError('Ava\'s native camera driver is not initialized.');
    }
    unawaited(
      webViewController
          .evaluateJavascript(source: _recenterCameraJavaScript())
          .catchError(
        (Object error, StackTrace stackTrace) {
          if (!_disposed) {
            _onCommandError?.call(
              'Ava could not return to her front-facing position: $error',
            );
          }
          return null;
        },
      ),
    );
  }

  @override
  int playAnimation({
    required String animationName,
    required int loopCount,
    double playbackRate = 1,
  }) {
    _ensureModelReady();
    if (animationName.isEmpty) {
      throw ArgumentError.value(
        animationName,
        'animationName',
        'must not be empty',
      );
    }
    if (loopCount < 0) {
      throw ArgumentError.value(
        loopCount,
        'loopCount',
        'must not be negative',
      );
    }
    if (!playbackRate.isFinite || playbackRate <= 0) {
      throw ArgumentError.value(
        playbackRate,
        'playbackRate',
        'must be finite and greater than zero',
      );
    }

    final playbackId = ++_nextPlaybackId;
    if (kIsWeb) {
      if (loopCount > 0) {
        throw UnsupportedError(
          'Finite animation completion is not implemented for Flutter web.',
        );
      }
      _delegate.playAnimation(
        animationName: animationName,
        loopCount: loopCount,
      );
      return playbackId;
    }

    final webViewController = _webViewController;
    final viewerId = _viewerId;
    final finishedHandlerName = _animationFinishedHandlerName;
    if (webViewController == null ||
        viewerId == null ||
        finishedHandlerName == null) {
      throw StateError('Ava\'s native animation driver is not initialized.');
    }

    final source = _animationCommandJavaScript(
      viewerId: viewerId,
      finishedHandlerName: finishedHandlerName,
      animationName: animationName,
      playbackId: playbackId,
      loopCount: loopCount,
      playbackRate: playbackRate,
    );
    unawaited(
      webViewController.evaluateJavascript(source: source).catchError(
        (Object error, StackTrace stackTrace) {
          if (!_disposed) {
            _onCommandError?.call(
              'Ava could not execute animation $animationName: $error',
            );
          }
          return null;
        },
      ),
    );
    return playbackId;
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _modelLoaded = false;
    _webViewController = null;
    _onCommandError = null;
    _delegate.onModelLoaded.dispose();
  }

  void _ensureModelReady() {
    _ensureNotDisposed();
    if (!_modelLoaded) {
      throw StateError('Ava\'s 3D model is not ready.');
    }
  }

  void _ensureNotDisposed() {
    if (_disposed) {
      throw StateError('Ava3DController is disposed.');
    }
  }

  static String _animationCommandJavaScript({
    required String viewerId,
    required String finishedHandlerName,
    required String animationName,
    required int playbackId,
    required int loopCount,
    required double playbackRate,
  }) {
    final encodedViewerId = jsonEncode(viewerId);
    final encodedHandlerName = jsonEncode(finishedHandlerName);
    final encodedAnimationName = jsonEncode(animationName);
    final repetitions = loopCount == 0 ? 'Infinity' : loopCount.toString();
    final listensForCompletion = loopCount > 0;
    return '''
      (() => {
        const viewer = document.getElementById($encodedViewerId);
        if (!viewer) throw new Error('Ava model-viewer element was not found.');

        if (window.__avaAnimationFinishedListener) {
          viewer.removeEventListener(
            'finished',
            window.__avaAnimationFinishedListener
          );
          window.__avaAnimationFinishedListener = null;
        }

        const playback = Object.freeze({
          id: $playbackId,
          name: $encodedAnimationName
        });
        window.__avaActivePlayback = playback;

        if ($listensForCompletion) {
          const finishedListener = () => {
            const active = window.__avaActivePlayback;
            if (!active || active.id !== playback.id) return;
            viewer.removeEventListener('finished', finishedListener);
            window.__avaAnimationFinishedListener = null;
            const bridge = window.flutter_inappwebview;
            if (!bridge || typeof bridge.callHandler !== 'function') {
              throw new Error('Ava animation bridge is unavailable.');
            }
            bridge.callHandler(
              $encodedHandlerName,
              playback.id,
              playback.name
            );
          };
          window.__avaAnimationFinishedListener = finishedListener;
          viewer.addEventListener('finished', finishedListener);
        }

        viewer.pause();
        viewer.timeScale = $playbackRate;
        if (viewer.animationName === playback.name) {
          viewer.currentTime = 0;
        } else {
          viewer.animationName = playback.name;
        }
        return Promise.resolve(viewer.updateComplete).then(() => {
          const active = window.__avaActivePlayback;
          if (!active || active.id !== playback.id) return;
          viewer.currentTime = 0;
          viewer.play({repetitions: $repetitions});
        });
      })();
    ''';
  }

  static String _recenterCameraJavaScript() {
    return '''
      (() => {
        const cameraBridge = window.__avaCameraBridge;
        if (!cameraBridge || typeof cameraBridge.recenter !== 'function') {
          throw new Error('Ava camera bridge is unavailable.');
        }
        cameraBridge.recenter('conversation');
      })();
    ''';
  }
}

/// Model-viewer adapter that owns native gesture arbitration and animation
/// completion events.
class Ava3DViewer extends StatefulWidget {
  const Ava3DViewer({
    required this.controller,
    required this.src,
    required this.onSurfaceTap,
    required this.onAnimationFinished,
    this.gestureConfiguration = const AvaViewerGestureConfiguration(),
    this.animationCrossfadeDuration = const Duration(milliseconds: 180),
    this.progressBarColor,
    this.onProgress,
    this.onLoad,
    this.onError,
    super.key,
  });

  final Ava3DController controller;
  final String src;
  final ValueChanged<AvaViewerPointerEvent> onSurfaceTap;
  final ValueChanged<AvaAnimationFinishedEvent> onAnimationFinished;
  final AvaViewerGestureConfiguration gestureConfiguration;
  final Duration animationCrossfadeDuration;
  final Color? progressBarColor;
  final ValueChanged<double>? onProgress;
  final ValueChanged<String>? onLoad;
  final ValueChanged<String>? onError;

  @override
  State<Ava3DViewer> createState() => _Ava3DViewerState();
}

class _Ava3DViewerState extends State<Ava3DViewer> {
  static const _bridgeKey = 'flutter-3d-controller';

  late final String _viewerId;
  late final String _pointerHandlerName;
  late final String _animationFinishedHandlerName;
  late final String _interactionErrorHandlerName;
  late final String _relatedJavaScript;

  @override
  void initState() {
    super.initState();
    if (widget.gestureConfiguration.maximumTapDuration <= Duration.zero) {
      throw ArgumentError.value(
        widget.gestureConfiguration.maximumTapDuration,
        'gestureConfiguration.maximumTapDuration',
        'must be greater than zero',
      );
    }
    if (widget.gestureConfiguration.autoCenterDelay <= Duration.zero) {
      throw ArgumentError.value(
        widget.gestureConfiguration.autoCenterDelay,
        'gestureConfiguration.autoCenterDelay',
        'must be greater than zero',
      );
    }
    if (widget.animationCrossfadeDuration.isNegative) {
      throw ArgumentError.value(
        widget.animationCrossfadeDuration,
        'animationCrossfadeDuration',
        'must not be negative',
      );
    }
    final utils = Utils();
    _viewerId = utils.generateId();
    _pointerHandlerName = 'ava-pointer-$_viewerId';
    _animationFinishedHandlerName = 'ava-animation-finished-$_viewerId';
    _interactionErrorHandlerName = 'ava-interaction-error-$_viewerId';
    _relatedJavaScript = [
      utils.injectedJS(_viewerId, _bridgeKey),
      _pointerBridgeJavaScript(
        viewerId: _viewerId,
        handlerName: _pointerHandlerName,
        errorHandlerName: _interactionErrorHandlerName,
        configuration: widget.gestureConfiguration,
      ),
    ].join('\n');

    if (kIsWeb) {
      widget.controller.initializeWeb(_viewerId);
    }
  }

  @override
  Widget build(BuildContext context) {
    final gestures = widget.gestureConfiguration;
    return ModelViewer(
      id: _viewerId,
      src: widget.src,
      progressBarColor: widget.progressBarColor,
      relatedJs: _relatedJavaScript,
      interactionPrompt: InteractionPrompt.none,
      activeGestureInterceptor: false,
      cameraControls: true,
      disablePan: true,
      disableTap: true,
      disableZoom: true,
      touchAction: TouchAction.none,
      orbitSensitivity: gestures.orbitSensitivity,
      interpolationDecay: gestures.interpolationDecay,
      minCameraOrbit: 'auto ${gestures.minimumPolarAngleDegrees}deg auto',
      maxCameraOrbit: 'auto ${gestures.maximumPolarAngleDegrees}deg auto',
      animationCrossfadeDuration:
          widget.animationCrossfadeDuration.inMilliseconds,
      ar: false,
      autoPlay: false,
      autoRotate: false,
      debugLogging: false,
      onProgress: widget.onProgress,
      onLoad: (modelAddress) {
        widget.controller.markModelLoaded(true);
        widget.onLoad?.call(modelAddress);
      },
      onError: (error) {
        widget.controller.markModelLoaded(false);
        widget.onError?.call(error);
      },
      onWebViewCreated: kIsWeb
          ? null
          : (webViewController) {
              widget.controller.initializeNative(
                viewerId: _viewerId,
                animationFinishedHandlerName: _animationFinishedHandlerName,
                webViewController: webViewController,
                onCommandError: (message) => widget.onError?.call(message),
              );
              _registerJavaScriptHandler(
                webViewController: webViewController,
                handlerName: _pointerHandlerName,
                decode: AvaViewerPointerEvent.fromChannelArguments,
                onValue: widget.onSurfaceTap,
                eventDescription: 'surface-tap',
              );
              _registerJavaScriptHandler(
                webViewController: webViewController,
                handlerName: _animationFinishedHandlerName,
                decode: AvaAnimationFinishedEvent.fromChannelArguments,
                onValue: widget.onAnimationFinished,
                eventDescription: 'animation-finished',
              );
              _registerJavaScriptHandler(
                webViewController: webViewController,
                handlerName: _interactionErrorHandlerName,
                decode: _decodeInteractionError,
                onValue: (message) => widget.onError?.call(
                  'Ava\'s 3D interaction failed: $message',
                ),
                eventDescription: 'interaction-error',
              );
            },
    );
  }

  String _decodeInteractionError(List<dynamic> arguments) {
    if (arguments.length != 1) {
      throw FormatException(
        'Expected one interaction error message; '
        'received ${arguments.length} values.',
      );
    }
    final message = arguments.single?.toString() ?? '';
    if (message.trim().isEmpty) {
      throw const FormatException(
        'Interaction error message must not be empty.',
      );
    }
    return message;
  }

  void _registerJavaScriptHandler<T>({
    required InAppWebViewController webViewController,
    required String handlerName,
    required T Function(List<dynamic>) decode,
    required ValueChanged<T> onValue,
    required String eventDescription,
  }) {
    webViewController.addJavaScriptHandler(
      handlerName: handlerName,
      callback: (arguments) {
        if (!mounted) return false;
        try {
          onValue(decode(arguments));
          return true;
        } catch (error, stackTrace) {
          final message =
              'Ava received invalid $eventDescription data from the 3D viewer: '
              '$error';
          FlutterError.reportError(
            FlutterErrorDetails(
              exception: error,
              stack: stackTrace,
              library: 'ava_3d_viewer',
              context: ErrorDescription(
                'while decoding a WebView $eventDescription event',
              ),
            ),
          );
          widget.onError?.call(message);
          return false;
        }
      },
    );
  }

  static String _pointerBridgeJavaScript({
    required String viewerId,
    required String handlerName,
    required String errorHandlerName,
    required AvaViewerGestureConfiguration configuration,
  }) {
    final encodedViewerId = jsonEncode(viewerId);
    final encodedHandlerName = jsonEncode(handlerName);
    final encodedErrorHandlerName = jsonEncode(errorHandlerName);
    final tapSlop = configuration.tapSlop;
    final maximumTapMilliseconds =
        configuration.maximumTapDuration.inMilliseconds;
    final autoCenterMilliseconds = configuration.autoCenterDelay.inMilliseconds;
    final frontPolarAngleDegrees = configuration.frontPolarAngleDegrees;
    return '''
      (() => {
        if (window.__avaPointerBridgeInstalled === true) return;
        window.__avaPointerBridgeInstalled = true;
        const viewer = document.getElementById($encodedViewerId);
        if (!viewer) throw new Error('Ava model-viewer element was not found.');
        const tapCandidates = new Map();
        let autoCenterTimer = null;

        const reportFailure = (error) => {
          const message = error instanceof Error ? error.message : String(error);
          const bridge = window.flutter_inappwebview;
          if (!bridge || typeof bridge.callHandler !== 'function') {
            console.error('Ava interaction bridge is unavailable:', message);
            return;
          }
          bridge.callHandler($encodedErrorHandlerName, message);
        };

        const clearAutoCenter = () => {
          if (autoCenterTimer !== null) {
            window.clearTimeout(autoCenterTimer);
            autoCenterTimer = null;
          }
        };

        const recenter = (reason) => {
          clearAutoCenter();
          try {
            const orbit = viewer.getCameraOrbit();
            if (!orbit || !Number.isFinite(orbit.radius)) {
              throw new Error('Ava camera returned an invalid orbit.');
            }
            viewer.cameraOrbit =
              '0deg ${frontPolarAngleDegrees}deg ' + orbit.radius + 'm';
          } catch (error) {
            reportFailure(error);
          }
        };

        const scheduleAutoCenter = () => {
          clearAutoCenter();
          autoCenterTimer = window.setTimeout(
            () => recenter('inactivity'),
            $autoCenterMilliseconds
          );
        };

        window.__avaCameraBridge = Object.freeze({recenter});

        document.addEventListener('pointerdown', (event) => {
          if (event.button !== 0) return;
          let modelHit;
          let surface;
          let cameraOrbit;
          try {
            if (typeof viewer.positionAndNormalFromPoint !== 'function') {
              throw new Error('Ava mesh hit testing is unavailable.');
            }
            if (typeof viewer.surfaceFromPoint !== 'function') {
              throw new Error('Ava surface identity picking is unavailable.');
            }
            modelHit = viewer.positionAndNormalFromPoint(
              event.clientX,
              event.clientY
            );
            surface = viewer.surfaceFromPoint(event.clientX, event.clientY);
            cameraOrbit = viewer.getCameraOrbit();
            if (!cameraOrbit ||
                !Number.isFinite(cameraOrbit.theta) ||
                !Number.isFinite(cameraOrbit.phi) ||
                !Number.isFinite(cameraOrbit.radius) ||
                cameraOrbit.radius <= 0) {
              throw new Error('Ava camera returned an invalid orbit.');
            }
          } catch (error) {
            reportFailure(error);
            tapCandidates.delete(event.pointerId);
            event.preventDefault();
            event.stopPropagation();
            return;
          }

          if (modelHit === null || surface === null) {
            tapCandidates.delete(event.pointerId);
            event.preventDefault();
            event.stopPropagation();
            return;
          }

          clearAutoCenter();
          tapCandidates.set(event.pointerId, {
            pointerId: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            startedAt: performance.now(),
            moved: false,
            peakPressure: Number.isFinite(event.pressure) ? event.pressure : 0,
            pointerType: event.pointerType || 'unknown',
            modelPosition: modelHit.position,
            modelNormal: modelHit.normal,
            surface,
            cameraAzimuth: cameraOrbit.theta,
            cameraElevation: cameraOrbit.phi,
            cameraRadius: cameraOrbit.radius,
            avatarRootYaw: Number.isFinite(window.__avaAvatarRootYawRadians)
              ? window.__avaAvatarRootYawRadians
              : 0
          });
          if (event.isPrimary === false) {
            event.stopPropagation();
          }
        }, {capture: true, passive: false});

        document.addEventListener('pointermove', (event) => {
          const tapCandidate = tapCandidates.get(event.pointerId);
          if (!tapCandidate) return;
          const distance = Math.hypot(
            event.clientX - tapCandidate.x,
            event.clientY - tapCandidate.y
          );
          if (distance > $tapSlop) tapCandidate.moved = true;
          if (Number.isFinite(event.pressure)) {
            tapCandidate.peakPressure = Math.max(
              tapCandidate.peakPressure,
              event.pressure
            );
          }
        }, {capture: true, passive: true});

        document.addEventListener('pointerup', (event) => {
          const candidate = tapCandidates.get(event.pointerId);
          if (!candidate) return;
          tapCandidates.delete(event.pointerId);
          scheduleAutoCenter();
          const elapsed = performance.now() - candidate.startedAt;
          if (candidate.moved || elapsed > $maximumTapMilliseconds) return;
          const bridge = window.flutter_inappwebview;
          if (!bridge || typeof bridge.callHandler !== 'function') {
            reportFailure('Ava pointer bridge is unavailable.');
            return;
          }
          bridge.callHandler(
            $encodedHandlerName,
            event.clientX,
            event.clientY,
            window.innerWidth,
            window.innerHeight,
            candidate.modelPosition.x,
            candidate.modelPosition.y,
            candidate.modelPosition.z,
            candidate.modelNormal.x,
            candidate.modelNormal.y,
            candidate.modelNormal.z,
            candidate.surface,
            elapsed,
            candidate.peakPressure,
            candidate.pointerType,
            candidate.cameraAzimuth,
            candidate.cameraElevation,
            candidate.cameraRadius,
            candidate.avatarRootYaw
          );
        }, {capture: true, passive: true});

        document.addEventListener('pointercancel', (event) => {
          if (tapCandidates.has(event.pointerId)) {
            tapCandidates.delete(event.pointerId);
            scheduleAutoCenter();
          }
        }, {capture: true, passive: true});
      })();
    ''';
  }
}
