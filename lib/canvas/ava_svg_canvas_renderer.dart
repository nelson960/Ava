import 'dart:convert';

import '../models/ava_svg_canvas_protocol.dart';

final RegExp _handlerName = RegExp(r'^[A-Za-z][A-Za-z0-9_-]{0,95}$');

final class AvaSvgRendererBridgeMessage {
  const AvaSvgRendererBridgeMessage._(this.fields);

  final Map<String, Object?> fields;

  factory AvaSvgRendererBridgeMessage.fromArguments(
    List<dynamic> arguments,
  ) {
    if (arguments.length != 1 || arguments.single is! Map) {
      throw const FormatException(
        'SVG renderer bridge requires one JSON object.',
      );
    }
    return AvaSvgRendererBridgeMessage._(
      (arguments.single as Map).cast<String, Object?>(),
    );
  }

  String requiredString(String key, {int maximum = 160}) {
    final value = fields[key];
    if (value is! String || value.isEmpty || value.length > maximum) {
      throw FormatException('SVG renderer bridge.$key is invalid.');
    }
    return value;
  }
}

final class AvaSvgCanvasHtmlRenderer {
  const AvaSvgCanvasHtmlRenderer();

  String build({
    required AvaValidatedSvgCanvasDocument document,
    required String preparedHandler,
    required String presentedHandler,
    required String bindingHandler,
    required String errorHandler,
  }) {
    for (final handler in [
      preparedHandler,
      presentedHandler,
      bindingHandler,
      errorHandler,
    ]) {
      if (!_handlerName.hasMatch(handler)) {
        throw ArgumentError.value(handler, 'handler', 'invalid bridge name');
      }
    }
    final svgBase64 = base64Encode(document.svgBytes);
    final manifestBase64 = base64Encode(document.manifestBytes);
    final svgHash = document.descriptor.svgSha256;
    final manifestHash = document.descriptor.manifestSha256;
    final background =
        jsonEncode(document.manifest.background ?? 'transparent');
    return '''<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; connect-src 'none'; font-src 'none'; frame-src 'none'; img-src data:; media-src 'none'; object-src 'none'; script-src 'nonce-ava-svg-runtime-v1'; style-src 'unsafe-inline'">
<style>
html,body,#ava-host,#ava-overlay{position:absolute;inset:0;margin:0;width:100%;height:100%;overflow:hidden;background:transparent;touch-action:none;-webkit-user-select:none;user-select:none}
body{opacity:0;background:$background}
#ava-host svg{display:block;width:100%;height:100%}
#ava-overlay{pointer-events:none}
.ava-hit{position:absolute;box-sizing:border-box;min-width:44px;min-height:44px;border:0;padding:0;background:transparent;pointer-events:auto;touch-action:none;-webkit-tap-highlight-color:transparent}
.ava-hit.ava-selected{border:0;background:transparent;outline:0;box-shadow:none}
.ava-step-controls{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:18px;background:rgba(255,255,255,.92);pointer-events:auto}
.ava-step-controls button{min-width:44px;min-height:44px;border:0;border-radius:14px;background:#d7eeee;color:#173d40;font:600 15px system-ui}
.ava-step-controls button:disabled{opacity:.38}
.ava-step-count{min-width:52px;text-align:center;color:#173d40;font:600 14px system-ui}
</style></head><body>
<div id="ava-host" aria-label="${_escapeHtmlAttribute(document.manifest.title)}"></div>
<div id="ava-overlay"></div>
<script nonce="ava-svg-runtime-v1">
(() => {
  'use strict';
  const svgHash = '$svgHash';
  const manifestHash = '$manifestHash';
  const bridge = (name, value) => window.flutter_inappwebview.callHandler(name, value);
  const fail = (code) => bridge('$errorHandler', {code});
  const decodeUtf8 = (encoded) => {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new TextDecoder('utf-8', {fatal:true}).decode(bytes);
  };
  try {
    const svgText = decodeUtf8('$svgBase64');
    const manifest = JSON.parse(decodeUtf8('$manifestBase64'));
    const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    if (parsed.querySelector('parsererror') || parsed.documentElement.localName !== 'svg') {
      throw new Error('svgParse');
    }
    const host = document.getElementById('ava-host');
    const overlay = document.getElementById('ava-overlay');
    const svg = document.importNode(parsed.documentElement, true);
    host.replaceChildren(svg);
    const bindings = new Map();
    const interactionMode = manifest.interactionMode;
    let selectedBindingId = null;
    let currentStep = 1;
    let presented = false;
    let rebuildQueued = false;

    for (const binding of manifest.bindings) {
      const element = svg.querySelector('#' + CSS.escape(binding.elementId));
      if (!element || element.getAttribute('data-ava-binding') !== binding.bindingId) {
        throw new Error('bindingElementMismatch');
      }
      bindings.set(binding.bindingId, {binding, element});
      for (const id of [binding.elementId, binding.targetId, ...binding.revealIds]) {
        const node = svg.querySelector('#' + CSS.escape(id));
        if (!node) throw new Error('bindingTargetMissing');
      }
    }

    const show = (id) => {
      const node = svg.querySelector('#' + CSS.escape(id));
      if (!node) return;
      node.style.display = '';
      node.style.visibility = 'visible';
    };
    const hide = (id) => {
      const node = svg.querySelector('#' + CSS.escape(id));
      if (node) node.style.display = 'none';
    };
    const report = (binding) => bridge('$bindingHandler', {bindingId: binding.bindingId});
    const activate = (record) => {
      const binding = record.binding;
      if (interactionMode !== 'steps' && selectedBindingId === binding.bindingId) return;
      if (interactionMode === 'explore') {
        if (selectedBindingId && selectedBindingId !== binding.bindingId) {
          const previous = bindings.get(selectedBindingId);
          if (previous) for (const id of previous.binding.revealIds) hide(id);
        }
        for (const id of binding.revealIds) show(id);
      }
      selectedBindingId = binding.bindingId;
      report(binding);
      queueRebuild();
    };
    const stepBindings = [...bindings.values()]
      .filter((record) => Number.isInteger(record.binding.stepIndex))
      .sort((left, right) => left.binding.stepIndex - right.binding.stepIndex);
    const applyStep = (step, shouldReport) => {
      if (interactionMode !== 'steps' || step < 1 || step > stepBindings.length) return;
      currentStep = step;
      for (const record of stepBindings) {
        if (record.binding.stepIndex === currentStep) show(record.binding.elementId);
        else hide(record.binding.elementId);
      }
      selectedBindingId = stepBindings[currentStep - 1].binding.bindingId;
      if (shouldReport) report(stepBindings[currentStep - 1].binding);
      queueRebuild();
    };

    const rebuild = () => {
      rebuildQueued = false;
      overlay.replaceChildren();
      if (interactionMode !== 'steps') {
        for (const record of bindings.values()) {
          const rect = record.element.getBoundingClientRect();
          if (!Number.isFinite(rect.left) || !Number.isFinite(rect.top)) continue;
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'ava-hit';
          button.setAttribute('aria-label', record.binding.accessibilityLabel);
          button.dataset.bindingId = record.binding.bindingId;
          const width = Math.max(44, rect.width);
          const height = Math.max(44, rect.height);
          button.style.left = (rect.left + rect.width / 2 - width / 2) + 'px';
          button.style.top = (rect.top + rect.height / 2 - height / 2) + 'px';
          button.style.width = width + 'px';
          button.style.height = height + 'px';
          if (record.binding.bindingId === selectedBindingId) button.classList.add('ava-selected');
          button.addEventListener('click', () => activate(record));
          overlay.appendChild(button);
        }
      }
      if (interactionMode === 'steps') {
        const controls = document.createElement('div');
        controls.className = 'ava-step-controls';
        const back = document.createElement('button');
        back.type = 'button'; back.textContent = 'Back'; back.disabled = currentStep === 1;
        back.addEventListener('click', () => applyStep(currentStep - 1, true));
        const count = document.createElement('span');
        count.className = 'ava-step-count'; count.textContent = currentStep + ' / ' + stepBindings.length;
        const next = document.createElement('button');
        next.type = 'button'; next.textContent = 'Next'; next.disabled = currentStep === stepBindings.length;
        next.addEventListener('click', () => applyStep(currentStep + 1, true));
        controls.append(back, count, next);
        overlay.appendChild(controls);
      }
    };
    function queueRebuild() {
      if (rebuildQueued) return;
      rebuildQueued = true;
      requestAnimationFrame(rebuild);
    }
    new ResizeObserver(queueRebuild).observe(host);
    if (interactionMode === 'steps') applyStep(1, false);
    queueRebuild();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      bridge('$preparedHandler', {svgSha256:svgHash, manifestSha256:manifestHash});
    }));
    window.avaPresent = () => {
      if (presented) return;
      presented = true;
      document.body.style.opacity = '1';
      requestAnimationFrame(() => requestAnimationFrame(() => {
        bridge('$presentedHandler', {svgSha256:svgHash, manifestSha256:manifestHash});
      }));
    };
  } catch (_) {
    fail('rendererPreparationFailed');
  }
})();
</script></body></html>''';
  }
}

String _escapeHtmlAttribute(String value) => value
    .replaceAll('&', '&amp;')
    .replaceAll('"', '&quot;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
