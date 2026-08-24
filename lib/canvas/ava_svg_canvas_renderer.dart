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
.ava-hit.ava-feedback{border:2px solid rgba(33,126,132,.5);border-radius:14px;background:rgba(255,255,255,.12);animation:ava-pulse .55s ease-out}
.ava-connector-pulse{animation:ava-connector-pulse .65s ease-out}
@keyframes ava-pulse{0%{transform:scale(.92);opacity:.5}100%{transform:scale(1.08);opacity:0}}
@keyframes ava-connector-pulse{0%,100%{opacity:1}50%{opacity:.28}}
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
    const original = new Map();
    let selectedExplore = null;
    let presented = false;
    let rebuildQueued = false;

    for (const binding of manifest.bindings) {
      const element = svg.querySelector('#' + CSS.escape(binding.elementId));
      if (!element || element.getAttribute('data-ava-binding') !== binding.bindingId) {
        throw new Error('bindingElementMismatch');
      }
      bindings.set(binding.bindingId, {binding, element});
      for (const id of [binding.elementId, binding.targetId, ...binding.revealIds, ...binding.pulseIds]) {
        const node = svg.querySelector('#' + CSS.escape(id));
        if (!node) throw new Error('bindingTargetMissing');
        if (!original.has(id)) {
          original.set(id, {
            display: node.style.display,
            visibility: node.style.visibility,
            transform: node.getAttribute('transform')
          });
        }
      }
    }

    const restore = (id) => {
      const node = svg.querySelector('#' + CSS.escape(id));
      const saved = original.get(id);
      if (!node || !saved) return;
      node.style.display = saved.display;
      node.style.visibility = saved.visibility;
      if (saved.transform === null) node.removeAttribute('transform');
      else node.setAttribute('transform', saved.transform);
    };
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
    const report = (binding) => {
      if (binding.serverReportable) {
        bridge('$bindingHandler', {bindingId: binding.bindingId});
      }
    };
    const feedback = (button) => {
      button.classList.remove('ava-feedback');
      void button.offsetWidth;
      button.classList.add('ava-feedback');
      setTimeout(() => button.classList.remove('ava-feedback'), 600);
    };
    const activate = (record, button) => {
      const binding = record.binding;
      if (binding.action === 'reset') {
        for (const id of original.keys()) restore(id);
        selectedExplore = null;
      } else if (binding.action === 'toggleVisibility') {
        const target = svg.querySelector('#' + CSS.escape(binding.targetId));
        if (target) target.style.display = target.style.display === 'none' ? '' : 'none';
      } else {
        if (binding.action === 'explore' && binding.exclusive && selectedExplore && selectedExplore !== binding.bindingId) {
          const previous = bindings.get(selectedExplore);
          if (previous) for (const id of previous.binding.revealIds) hide(id);
        }
        for (const id of binding.revealIds) show(id);
        for (const id of binding.pulseIds) {
          const connector = svg.querySelector('#' + CSS.escape(id));
          if (!connector) continue;
          connector.classList.remove('ava-connector-pulse');
          void connector.getBoundingClientRect();
          connector.classList.add('ava-connector-pulse');
          setTimeout(() => connector.classList.remove('ava-connector-pulse'), 700);
        }
        if (binding.action === 'explore') selectedExplore = binding.bindingId;
      }
      feedback(button);
      report(binding);
      queueRebuild();
    };
    const toSvgPoint = (x, y) => {
      const point = svg.createSVGPoint();
      point.x = x; point.y = y;
      const matrix = svg.getScreenCTM();
      return matrix ? point.matrixTransform(matrix.inverse()) : point;
    };

    const rebuild = () => {
      rebuildQueued = false;
      overlay.replaceChildren();
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
        if (record.binding.action === 'dragWithin') {
          let drag = null;
          button.addEventListener('pointerdown', (event) => {
            button.setPointerCapture(event.pointerId);
            drag = {start: toSvgPoint(event.clientX, event.clientY), moved:false};
          });
          button.addEventListener('pointermove', (event) => {
            if (!drag) return;
            const current = toSvgPoint(event.clientX, event.clientY);
            const dx = current.x - drag.start.x;
            const dy = current.y - drag.start.y;
            const saved = original.get(record.binding.targetId);
            const target = svg.querySelector('#' + CSS.escape(record.binding.targetId));
            if (!target || !saved) return;
            const prefix = saved.transform ? saved.transform + ' ' : '';
            target.setAttribute('transform', prefix + 'translate(' + dx + ' ' + dy + ')');
            button.style.transform = 'translate(' + (event.clientX - rect.left - rect.width / 2) + 'px,' + (event.clientY - rect.top - rect.height / 2) + 'px)';
            drag.moved = true;
          });
          button.addEventListener('pointerup', () => {
            if (drag && drag.moved) { feedback(button); report(record.binding); }
            drag = null;
            queueRebuild();
          });
          button.addEventListener('pointercancel', () => { drag = null; queueRebuild(); });
        } else {
          button.addEventListener('click', () => activate(record, button));
        }
        overlay.appendChild(button);
      }
    };
    function queueRebuild() {
      if (rebuildQueued) return;
      rebuildQueued = true;
      requestAnimationFrame(rebuild);
    }
    new ResizeObserver(queueRebuild).observe(host);
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
