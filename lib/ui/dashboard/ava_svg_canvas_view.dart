import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

import '../../canvas/ava_svg_canvas_renderer.dart';
import '../../models/ava_svg_canvas_protocol.dart';

typedef _DraftRendererIdentity = ({
  String generationId,
  int draftIndex,
  String svgSha256,
});

typedef _CandidateRendererIdentity = ({
  String generationId,
  String svgSha256,
  String manifestSha256,
});

typedef AvaSvgCanvasRendererErrorHandler = void Function(
  String message, {
  String? generationId,
});

/// One persistent, sandboxed renderer for the retained active scene and the
/// current generation's replaceable draft/final candidate layers.
final class AvaSvgCanvasView extends StatefulWidget {
  const AvaSvgCanvasView({
    super.key,
    required this.activeDocument,
    required this.draft,
    required this.candidate,
    required this.presentCandidate,
    required this.interactionsEnabled,
    required this.onDraftPresented,
    required this.onCandidatePrepared,
    required this.onCandidateFirstFramePresented,
    required this.onBinding,
    required this.onError,
  });

  final AvaValidatedSvgCanvasDocument? activeDocument;
  final AvaValidatedSvgCanvasDraft? draft;
  final AvaValidatedSvgCanvasDocument? candidate;
  final bool presentCandidate;
  final bool interactionsEnabled;
  final ValueChanged<AvaValidatedSvgCanvasDraft> onDraftPresented;
  final ValueChanged<AvaValidatedSvgCanvasDocument> onCandidatePrepared;
  final ValueChanged<AvaValidatedSvgCanvasDocument>
      onCandidateFirstFramePresented;
  final ValueChanged<AvaSvgInteractionBinding> onBinding;
  final AvaSvgCanvasRendererErrorHandler onError;

  @override
  State<AvaSvgCanvasView> createState() => _AvaSvgCanvasViewState();
}

class _AvaSvgCanvasViewState extends State<AvaSvgCanvasView> {
  static const _renderer = AvaSvgCanvasHtmlRenderer();

  late final String _preparedHandler;
  late final String _presentedHandler;
  late final String _draftPresentedHandler;
  late final String _bindingHandler;
  late final String _errorHandler;
  InAppWebViewController? _controller;
  Future<void> _operationTail = Future<void>.value();
  bool _runtimeReady = false;
  bool _unsupportedReported = false;
  String? _mountedActiveGenerationId;
  _DraftRendererIdentity? _visibleDraftIdentity;
  final List<_DraftRendererIdentity> _issuedDraftIdentities = [];
  final List<_CandidateRendererIdentity> _issuedPreparedIdentities = [];
  final List<_CandidateRendererIdentity> _issuedPresentedIdentities = [];
  String? _mountedCandidateGenerationId;
  String? _presentedCandidateGenerationId;
  bool? _activeInteractive;

  @override
  void initState() {
    super.initState();
    final suffix = identityHashCode(this).toRadixString(16);
    _preparedHandler = 'avaSvgPrepared_$suffix';
    _presentedHandler = 'avaSvgPresented_$suffix';
    _draftPresentedHandler = 'avaSvgDraftPresented_$suffix';
    _bindingHandler = 'avaSvgBinding_$suffix';
    _errorHandler = 'avaSvgError_$suffix';
  }

  @override
  void didUpdateWidget(AvaSvgCanvasView oldWidget) {
    super.didUpdateWidget(oldWidget);
    unawaited(_queueSync());
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
    final title = widget.activeDocument?.manifest.title ??
        widget.candidate?.manifest.title ??
        'Ava is drawing a visual';
    return Semantics(
      container: true,
      label: title,
      child: InAppWebView(
        initialData: InAppWebViewInitialData(
          data: _renderer.buildShell(
            preparedHandler: _preparedHandler,
            presentedHandler: _presentedHandler,
            draftPresentedHandler: _draftPresentedHandler,
            bindingHandler: _bindingHandler,
            errorHandler: _errorHandler,
          ),
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
        onLoadStop: (_, __) {
          _runtimeReady = true;
          unawaited(_queueSync());
        },
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
      callback: (arguments) => _handleCandidateIdentity(
        arguments,
        label: 'prepared',
        issuedIdentities: _issuedPreparedIdentities,
        callback: widget.onCandidatePrepared,
      ),
    );
    controller.addJavaScriptHandler(
      handlerName: _presentedHandler,
      callback: (arguments) => _handleCandidateIdentity(
        arguments,
        label: 'presented',
        issuedIdentities: _issuedPresentedIdentities,
        callback: widget.onCandidateFirstFramePresented,
      ),
    );
    controller.addJavaScriptHandler(
      handlerName: _draftPresentedHandler,
      callback: (arguments) {
        try {
          final message = AvaSvgRendererBridgeMessage.fromArguments(arguments);
          _requireExactFields(
            message,
            const {'generationId', 'draftIndex', 'svgSha256'},
            'draft presented',
          );
          final identity = (
            generationId: message.requiredString('generationId', maximum: 64),
            draftIndex:
                message.requiredInt('draftIndex', minimum: 1, maximum: 32),
            svgSha256: message.requiredString('svgSha256', maximum: 64),
          );
          if (!_issuedDraftIdentities.contains(identity)) {
            throw const FormatException(
              'SVG renderer reported a draft that was never issued.',
            );
          }
          final draft = widget.draft;
          if (draft == null || _draftIdentity(draft) != identity) return true;
          widget.onDraftPresented(draft);
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
          _requireExactFields(
            message,
            const {'bindingId', 'generationId'},
            'binding',
          );
          final active = widget.activeDocument;
          if (!widget.interactionsEnabled ||
              active == null ||
              message.requiredString('generationId', maximum: 64) !=
                  active.descriptor.generationId) {
            throw const FormatException(
              'SVG renderer emitted an interaction before activation.',
            );
          }
          final bindingId = message.requiredString('bindingId', maximum: 96);
          final binding = active.manifest.bindings[bindingId];
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
          _requireExactFields(
            message,
            const {'code', 'generationId'},
            'error',
          );
          final code = message.requiredString('code', maximum: 80);
          final generationId =
              message.requiredString('generationId', maximum: 64);
          widget.onError(
            'SVG renderer rejected the operation ($code).',
            generationId: generationId,
          );
          return true;
        } on FormatException catch (error) {
          widget.onError(error.message);
          return false;
        }
      },
    );
  }

  bool _handleCandidateIdentity(
    List<dynamic> arguments, {
    required String label,
    required List<_CandidateRendererIdentity> issuedIdentities,
    required ValueChanged<AvaValidatedSvgCanvasDocument> callback,
  }) {
    try {
      final message = AvaSvgRendererBridgeMessage.fromArguments(arguments);
      _requireExactFields(
        message,
        const {'generationId', 'svgSha256', 'manifestSha256'},
        label,
      );
      final identity = (
        generationId: message.requiredString('generationId', maximum: 64),
        svgSha256: message.requiredString('svgSha256', maximum: 64),
        manifestSha256: message.requiredString('manifestSha256', maximum: 64),
      );
      if (!issuedIdentities.contains(identity)) {
        throw FormatException(
          'SVG renderer $label identity was never issued.',
        );
      }
      final candidate = widget.candidate;
      if (candidate == null || _candidateIdentity(candidate) != identity) {
        return true;
      }
      callback(candidate);
      return true;
    } on FormatException catch (error) {
      widget.onError(error.message);
      return false;
    }
  }

  Future<void> _queueSync() {
    _operationTail = _operationTail.then((_) => _syncRenderer()).catchError(
      (Object error) {
        if (mounted) {
          widget.onError(
            'SVG renderer operation failed (${error.runtimeType}).',
          );
        }
      },
    );
    return _operationTail;
  }

  Future<void> _syncRenderer() async {
    if (!mounted || !_runtimeReady) return;
    final controller = _controller;
    if (controller == null) return;

    final active = widget.activeDocument;
    if (active != null &&
        _mountedActiveGenerationId != active.descriptor.generationId) {
      if (_mountedCandidateGenerationId == active.descriptor.generationId) {
        await _evaluate(
          controller,
          _renderer.activateCandidateScript(
            active,
            interactive: widget.interactionsEnabled,
          ),
        );
        _mountedCandidateGenerationId = null;
        _presentedCandidateGenerationId = null;
        _visibleDraftIdentity = null;
      } else {
        await _evaluate(
          controller,
          _renderer.mountActiveScript(
            active,
            interactive: widget.interactionsEnabled,
          ),
        );
      }
      _mountedActiveGenerationId = active.descriptor.generationId;
      _activeInteractive = widget.interactionsEnabled;
    } else if (active != null &&
        _activeInteractive != widget.interactionsEnabled) {
      await _evaluate(
        controller,
        _renderer.setActiveInteractiveScript(
          generationId: active.descriptor.generationId,
          interactive: widget.interactionsEnabled,
        ),
      );
      _activeInteractive = widget.interactionsEnabled;
    }

    final draft = widget.draft;
    final draftIdentity = draft == null ? null : _draftIdentity(draft);
    if (draft != null && _visibleDraftIdentity != draftIdentity) {
      _rememberIssued(_issuedDraftIdentities, draftIdentity!);
      await _evaluate(controller, _renderer.replaceDraftScript(draft));
      _visibleDraftIdentity = draftIdentity;
    }

    final candidate = widget.candidate;
    if (candidate != null &&
        _mountedCandidateGenerationId != candidate.descriptor.generationId) {
      _rememberIssued(
        _issuedPreparedIdentities,
        _candidateIdentity(candidate),
      );
      await _evaluate(controller, _renderer.mountCandidateScript(candidate));
      _mountedCandidateGenerationId = candidate.descriptor.generationId;
      _presentedCandidateGenerationId = null;
    }
    if (candidate != null &&
        widget.presentCandidate &&
        _presentedCandidateGenerationId != candidate.descriptor.generationId) {
      _rememberIssued(
        _issuedPresentedIdentities,
        _candidateIdentity(candidate),
      );
      await _evaluate(
        controller,
        _renderer.presentCandidateScript(candidate),
      );
      _presentedCandidateGenerationId = candidate.descriptor.generationId;
      _visibleDraftIdentity = null;
    }

    if (candidate == null && _mountedCandidateGenerationId != null) {
      final pendingGeneration = _mountedCandidateGenerationId!;
      if (_mountedActiveGenerationId != pendingGeneration) {
        await _evaluate(
          controller,
          _renderer.clearPendingScript(pendingGeneration),
        );
      }
      _mountedCandidateGenerationId = null;
      _presentedCandidateGenerationId = null;
      _visibleDraftIdentity = null;
    } else if (draft == null &&
        candidate == null &&
        _visibleDraftIdentity != null) {
      final pendingGeneration = _visibleDraftIdentity!.generationId;
      await _evaluate(
        controller,
        _renderer.clearPendingScript(pendingGeneration),
      );
      _visibleDraftIdentity = null;
    }
  }

  Future<void> _evaluate(
    InAppWebViewController controller,
    String source,
  ) async {
    await controller.evaluateJavascript(source: source);
  }
}

_DraftRendererIdentity _draftIdentity(AvaValidatedSvgCanvasDraft draft) => (
      generationId: draft.descriptor.generationId,
      draftIndex: draft.descriptor.draftIndex,
      svgSha256: draft.descriptor.svgSha256,
    );

_CandidateRendererIdentity _candidateIdentity(
  AvaValidatedSvgCanvasDocument document,
) =>
    (
      generationId: document.descriptor.generationId,
      svgSha256: document.descriptor.svgSha256,
      manifestSha256: document.descriptor.manifestSha256,
    );

void _rememberIssued<T>(List<T> identities, T identity) {
  if (identities.contains(identity)) return;
  identities.add(identity);
  if (identities.length > 32) identities.removeAt(0);
}

void _requireExactFields(
  AvaSvgRendererBridgeMessage message,
  Set<String> expected,
  String label,
) {
  final actual = message.fields.keys.toSet();
  if (actual.length != expected.length || !actual.containsAll(expected)) {
    throw FormatException('SVG renderer $label contains unknown fields.');
  }
}
