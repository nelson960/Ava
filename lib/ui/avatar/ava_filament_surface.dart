// Thermion's public widget double-destroys the previous Darwin descriptor
// after resize. Keep its implementation types isolated in this adapter until
// Ava owns a pinned renderer fork.
// ignore_for_file: implementation_imports

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:thermion_flutter/thermion_flutter.dart' as thermion;
import 'package:thermion_flutter/src/platform/src/platform_texture_descriptor.dart';
import 'package:thermion_flutter/src/widgets/src/thermion_widget_internal/surface_widget_builder.dart';

class AvaFilamentSurface extends StatefulWidget {
  const AvaFilamentSurface({
    required this.viewer,
    required this.onError,
    super.key,
  });

  final thermion.ThermionViewer viewer;
  final ValueChanged<Object> onError;

  @override
  State<AvaFilamentSurface> createState() => _AvaFilamentSurfaceState();
}

class _AvaFilamentSurfaceState extends State<AvaFilamentSurface> {
  static const _resizeDebounce = Duration(milliseconds: 100);

  PlatformTextureDescriptor? _texture;
  Timer? _resizeTimer;
  ({int width, int height})? _pendingSize;
  bool _allocationInProgress = false;
  bool _disposed = false;

  @override
  Widget build(BuildContext context) {
    final devicePixelRatio = MediaQuery.devicePixelRatioOf(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = (constraints.maxWidth * devicePixelRatio).ceil();
        final height = (constraints.maxHeight * devicePixelRatio).ceil();
        if (width <= 0 || height <= 0) return const SizedBox.shrink();
        final texture = _texture;
        if (texture == null ||
            texture.width != width ||
            texture.height != height) {
          _scheduleAllocation(width: width, height: height);
        }
        return surfaceWidgetBuilder(texture);
      },
    );
  }

  void _scheduleAllocation({required int width, required int height}) {
    _pendingSize = (width: width, height: height);
    if (_allocationInProgress) return;
    _resizeTimer?.cancel();
    _resizeTimer = Timer(_resizeDebounce, _drainPendingAllocation);
  }

  Future<void> _drainPendingAllocation() async {
    if (_disposed || _allocationInProgress) return;
    final requestedSize = _pendingSize;
    if (requestedSize == null) return;
    _pendingSize = null;
    final current = _texture;
    if (current != null &&
        current.width == requestedSize.width &&
        current.height == requestedSize.height) {
      return;
    }

    _allocationInProgress = true;
    if (current != null) {
      // resizeTexture owns destruction of the previous descriptor on Darwin.
      // Clearing our reference prevents dispose/build from destroying it too.
      _texture = null;
    }
    try {
      final next = current == null
          ? await thermion.ThermionFlutterPlugin.instance
              .createTextureAndBindToView(
              widget.viewer.view,
              requestedSize.width,
              requestedSize.height,
            )
          : await thermion.ThermionFlutterPlugin.instance.resizeTexture(
              current,
              widget.viewer.view,
              requestedSize.width,
              requestedSize.height,
            );
      if (next == null) {
        throw StateError('Filament did not create Ava\'s render texture.');
      }
      if (_disposed || !mounted) {
        await thermion.ThermionFlutterPlugin.instance
            .releaseTextureBindingForView(widget.viewer.view);
        await next.destroy();
        return;
      }
      await _configureViewport(next);
      setState(() => _texture = next);
    } catch (error, stackTrace) {
      FlutterError.reportError(
        FlutterErrorDetails(
          exception: error,
          stack: stackTrace,
          library: 'ava_filament_surface',
          context: ErrorDescription(
            'while allocating Ava\'s native Metal texture',
          ),
        ),
      );
      if (!_disposed) widget.onError(error);
    } finally {
      _allocationInProgress = false;
      if (!_disposed && _pendingSize != null) {
        _resizeTimer?.cancel();
        _resizeTimer = Timer(Duration.zero, _drainPendingAllocation);
      }
    }
  }

  Future<void> _configureViewport(PlatformTextureDescriptor descriptor) async {
    final view = widget.viewer.view;
    final camera = await view.getCamera();
    final near = await camera.getNear();
    final far = await camera.getCullingFar();
    final focalLength = await camera.getFocalLength();
    await camera.setLensProjection(
      near: near,
      far: far,
      focalLength: focalLength,
      aspect: descriptor.width / descriptor.height,
    );
    await view.setViewport(descriptor.width, descriptor.height);
  }

  @override
  void dispose() {
    _disposed = true;
    _resizeTimer?.cancel();
    _resizeTimer = null;
    _pendingSize = null;
    final texture = _texture;
    _texture = null;
    if (texture != null) {
      unawaited(() async {
        await thermion.ThermionFlutterPlugin.instance
            .releaseTextureBindingForView(widget.viewer.view);
        await texture.destroy();
      }());
    }
    super.dispose();
  }
}
