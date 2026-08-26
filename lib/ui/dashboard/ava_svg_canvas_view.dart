import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import '../../canvas/ava_svg_canvas_renderer.dart';
import '../../models/ava_svg_canvas_protocol.dart';

final class AvaSvgCanvasView extends StatefulWidget {
  const AvaSvgCanvasView({
    super.key,
    required this.document,
    required this.present,
    required this.onPrepared,
    required this.onFirstFramePresented,
    required this.onBinding,
    required this.onError,
  });

  final AvaValidatedSvgCanvasDocument document;
  final bool present;
  final VoidCallback onPrepared;
  final VoidCallback onFirstFramePresented;
  final ValueChanged<AvaSvgInteractionBinding> onBinding;
  final ValueChanged<String> onError;

  @override
  State<AvaSvgCanvasView> createState() => _AvaSvgCanvasViewState();
}

class _AvaSvgCanvasViewState extends State<AvaSvgCanvasView> {
  late final String _preparedHandler;
  late final String _presentedHandler;
  late final String _bindingHandler;
  late final String _errorHandler;
  InAppWebViewController? _controller;
  bool _prepared = false;
  bool _presentationRequested = false;
  bool _presented = false;
  bool _unsupportedReported = false;

  @override
  void initState() {
    super.initState();
    final suffix = identityHashCode(this).toRadixString(16);
    _preparedHandler = 'avaSvgPrepared_$suffix';
    _presentedHandler = 'avaSvgPresented_$suffix';
    _bindingHandler = 'avaSvgBinding_$suffix';
    _errorHandler = 'avaSvgError_$suffix';
  }

  @override
  void didUpdateWidget(AvaSvgCanvasView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.document.descriptor.svgSha256 !=
            widget.document.descriptor.svgSha256 ||
        oldWidget.document.descriptor.manifestSha256 !=
            widget.document.descriptor.manifestSha256) {
      _controller = null;
      _prepared = false;
      _presentationRequested = false;
      _presented = false;
      return;
    }
    if (!oldWidget.present && widget.present) {
      unawaited(_presentIfReady());
    }
  }

  @override
  Widget build(BuildContext context) {
    if (kIsWeb) {
      if (!_unsupportedReported) {
        _unsupportedReported = true;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) {
            widget.onError('SVG canvas WebView is unavailable on Flutter Web.');
          }
        });
      }
      return const SizedBox.shrink();
    }
    final html = const AvaSvgCanvasHtmlRenderer().build(
      document: widget.document,
      preparedHandler: _preparedHandler,
      presentedHandler: _presentedHandler,
      bindingHandler: _bindingHandler,
      errorHandler: _errorHandler,
    );
    return Semantics(
      container: true,
      label: widget.document.manifest.title,
      child: InAppWebView(
        key: ValueKey(
          '${widget.document.descriptor.svgSha256}:'
          '${widget.document.descriptor.manifestSha256}',
        ),
        initialData: InAppWebViewInitialData(
          data: html,
          mimeType: 'text/html',
          encoding: 'utf-8',
        ),
        initialSettings: InAppWebViewSettings(
          javaScriptEnabled: true,
          javaScriptCanOpenWindowsAutomatically: false,
          mediaPlaybackRequiresUserGesture: true,
          useShouldOverrideUrlLoading: true,
          useShouldInterceptRequest: true,
          transparentBackground: true,
          disableContextMenu: true,
          supportZoom: false,
          disableHorizontalScroll: true,
          disableVerticalScroll: true,
          horizontalScrollBarEnabled: false,
          verticalScrollBarEnabled: false,
          cacheEnabled: false,
          incognito: true,
          domStorageEnabled: false,
          databaseEnabled: false,
          geolocationEnabled: false,
          allowContentAccess: false,
          allowFileAccess: false,
          allowFileAccessFromFileURLs: false,
          allowUniversalAccessFromFileURLs: false,
          thirdPartyCookiesEnabled: false,
          isInspectable: false,
        ),
        onWebViewCreated: _onWebViewCreated,
        shouldOverrideUrlLoading: (_, action) async {
          final value = action.request.url?.toString();
          return value == null || value == 'about:blank'
              ? NavigationActionPolicy.ALLOW
              : NavigationActionPolicy.CANCEL;
        },
        shouldInterceptRequest: (_, __) async => WebResourceResponse(
          contentType: 'text/plain',
          contentEncoding: 'utf-8',
          data: Uint8List(0),
          statusCode: 403,
          reasonPhrase: 'Forbidden',
          headers: const {'Cache-Control': 'no-store'},
        ),
        onReceivedError: (_, request, error) {
          if (request.isForMainFrame == true) {
            widget.onError(
              'SVG renderer failed to load (${error.type.toNativeValue()}).',
            );
          }
        },
        onWebContentProcessDidTerminate: (_) {
          widget.onError('SVG renderer process terminated.');
        },
      ),
    );
  }

  void _onWebViewCreated(InAppWebViewController controller) {
    _controller = controller;
    controller.addJavaScriptHandler(
      handlerName: _preparedHandler,
      callback: (arguments) {
        try {
          _validateContentIdentity(arguments, label: 'prepared');
          if (_prepared) return true;
          _prepared = true;
          widget.onPrepared();
          unawaited(_presentIfReady());
          return true;
        } on FormatException catch (error) {
          widget.onError(error.message);
          return false;
        }
      },
    );
    controller.addJavaScriptHandler(
      handlerName: _presentedHandler,
      callback: (arguments) {
        try {
          _validateContentIdentity(arguments, label: 'presented');
          if (!_presentationRequested || _presented) {
            throw const FormatException(
              'SVG renderer sent canvasPresented in an invalid state.',
            );
          }
          _presented = true;
          widget.onFirstFramePresented();
          return true;
        } on FormatException catch (error) {
          widget.onError(error.message);
          return false;
        }
      },
    );
    controller.addJavaScriptHandler(
      handlerName: _bindingHandler,
      callback: (arguments) {
        try {
          final message = AvaSvgRendererBridgeMessage.fromArguments(arguments);
          if (message.fields.keys
                  .toSet()
                  .difference({'bindingId'}).isNotEmpty ||
              message.fields.length != 1) {
            throw const FormatException(
              'SVG renderer binding event contains unknown fields.',
            );
          }
          final bindingId = message.requiredString('bindingId', maximum: 96);
          final binding = widget.document.manifest.bindings[bindingId];
          if (binding == null) {
            throw const FormatException(
              'SVG renderer reported an unknown binding.',
            );
          }
          widget.onBinding(binding);
          return true;
        } on FormatException catch (error) {
          widget.onError(error.message);
          return false;
        }
      },
    );
    controller.addJavaScriptHandler(
      handlerName: _errorHandler,
      callback: (arguments) {
        try {
          final message = AvaSvgRendererBridgeMessage.fromArguments(arguments);
          if (message.fields.keys.toSet().difference({'code'}).isNotEmpty ||
              message.fields.length != 1) {
            throw const FormatException(
              'SVG renderer error contains unknown fields.',
            );
          }
          final code = message.requiredString('code', maximum: 80);
          widget.onError('SVG renderer rejected the document ($code).');
          return true;
        } on FormatException catch (error) {
          widget.onError(error.message);
          return false;
        }
      },
    );
  }

  void _validateContentIdentity(
    List<dynamic> arguments, {
    required String label,
  }) {
    final message = AvaSvgRendererBridgeMessage.fromArguments(arguments);
    if (message.fields.keys.toSet().difference({
          'svgSha256',
          'manifestSha256',
        }).isNotEmpty ||
        message.fields.length != 2 ||
        message.requiredString('svgSha256', maximum: 64) !=
            widget.document.descriptor.svgSha256 ||
        message.requiredString('manifestSha256', maximum: 64) !=
            widget.document.descriptor.manifestSha256) {
      throw FormatException(
        'SVG renderer $label content identity does not match the document.',
      );
    }
  }

  Future<void> _presentIfReady() async {
    if (!mounted || !widget.present || !_prepared || _presentationRequested) {
      return;
    }
    final controller = _controller;
    if (controller == null) {
      widget.onError('SVG renderer lost its controller before presentation.');
      return;
    }
    _presentationRequested = true;
    try {
      await controller.evaluateJavascript(
        source: 'window.avaPresent && window.avaPresent();',
      );
    } catch (error) {
      _presentationRequested = false;
      widget.onError(
        'SVG renderer could not present the prepared document '
        '(${error.runtimeType}).',
      );
    }
  }
}
