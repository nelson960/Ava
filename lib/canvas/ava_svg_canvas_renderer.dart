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

  int requiredInt(String key, {required int minimum, required int maximum}) {
    final value = fields[key];
    if (value is! int || value < minimum || value > maximum) {
      throw FormatException('SVG renderer bridge.$key is invalid.');
    }
    return value;
  }
}

/// Builds one app-owned sandbox runtime. Provider SVG is passed only as
/// base64 data to explicit replacement/mount operations and is never executed
/// as JavaScript.
final class AvaSvgCanvasHtmlRenderer {
  const AvaSvgCanvasHtmlRenderer();

  String buildShell({
    required String preparedHandler,
    required String presentedHandler,
    required String draftPresentedHandler,
    required String bindingHandler,
    required String errorHandler,
  }) {
    for (final handler in [
      preparedHandler,
      presentedHandler,
      draftPresentedHandler,
      bindingHandler,
      errorHandler,
    ]) {
      if (!_handlerName.hasMatch(handler)) {
        throw ArgumentError.value(handler, 'handler', 'invalid bridge name');
      }
    }
    return '''<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; connect-src 'none'; font-src 'none'; frame-src blob:; img-src data:; media-src 'none'; object-src 'none'; script-src 'nonce-ava-svg-runtime-v2'; style-src 'unsafe-inline'">
<style>
html,body,#ava-root,.ava-layer,.ava-host,.ava-overlay{position:absolute;inset:0;margin:0;width:100%;height:100%;overflow:hidden;background:transparent;touch-action:none;-webkit-user-select:none;user-select:none}
body{background:transparent}
.ava-layer{display:none;pointer-events:none}
.ava-layer.ava-visible{display:block}
.ava-document{position:absolute;inset:0;width:100%;height:100%;border:0;background:transparent;pointer-events:none}
.ava-overlay{pointer-events:none}
.ava-overlay.ava-interactive{pointer-events:auto}
.ava-hit{position:absolute;box-sizing:border-box;min-width:44px;min-height:44px;border:0;padding:0;background:transparent;pointer-events:auto;touch-action:none;-webkit-tap-highlight-color:transparent}
.ava-hit.ava-selected{border:0;background:transparent;outline:0;box-shadow:none}
.ava-step-controls{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:18px;background:rgba(255,255,255,.92);pointer-events:auto}
.ava-step-controls button{min-width:44px;min-height:44px;border:0;border-radius:14px;background:#d7eeee;color:#173d40;font:600 15px system-ui}
.ava-step-controls button:disabled{opacity:.38}
.ava-step-count{min-width:52px;text-align:center;color:#173d40;font:600 14px system-ui}
</style></head><body>
<div id="ava-root">
  <div id="ava-active" class="ava-layer"><div class="ava-host"></div><div class="ava-overlay"></div></div>
  <div id="ava-draft" class="ava-layer"><div class="ava-host"></div></div>
  <div id="ava-candidate" class="ava-layer"><div class="ava-host"></div><div class="ava-overlay"></div></div>
</div>
<script nonce="ava-svg-runtime-v2">
(() => {
  'use strict';
  const bridge = (name, value) => window.flutter_inappwebview.callHandler(name, value);
  const diagnose = (stage, identity) => console.debug('avaCanvas|' + stage + '|' + ((identity && identity.generationId) || 'none'));
  const fail = (code, identity) => { diagnose('rendererError.' + code, identity); return bridge('$errorHandler', {code, generationId:identity.generationId}); };
  const layer = (id) => {
    const root = document.getElementById(id);
    return {root, host:root.querySelector('.ava-host'), overlay:root.querySelector('.ava-overlay'), identity:null, runtime:null, frame:null, document:null, svg:null, blobUrl:null};
  };
  let active = layer('ava-active');
  const draft = layer('ava-draft');
  let candidate = layer('ava-candidate');
  const decodeUtf8 = (encoded) => {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new TextDecoder('utf-8', {fatal:true}).decode(bytes);
  };
  const parseSvg = (encoded) => {
    const parsed = new DOMParser().parseFromString(decodeUtf8(encoded), 'image/svg+xml');
    if (parsed.querySelector('parsererror') || parsed.documentElement.localName !== 'svg') throw new Error('svgParse');
    return document.importNode(parsed.documentElement, true);
  };
  const frameShell = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; connect-src 'none'; font-src 'none'; frame-src 'none'; img-src data:; media-src 'none'; object-src 'none'; script-src 'none'; style-src 'unsafe-inline'"><style>html,body{position:absolute;inset:0;margin:0;width:100%;height:100%;overflow:hidden;background:transparent}svg{display:block;width:100%;height:100%}</style></head><body></body></html>`;
  const scopedElement = (target, id) => {
    const element = target.document ? target.document.getElementById(id) : null;
    return element && target.svg && target.svg.contains(element) ? element : null;
  };
  const verifyScopedReferences = (scopedDocument, svg) => {
    const ids = [];
    const urlReference = /url\\(\\s*["']?#([A-Za-z_][A-Za-z0-9_.:-]*)["']?\\s*\\)/g;
    const urlAttributes = new Set(['fill','stroke','clip-path','mask','filter','marker-start','marker-mid','marker-end','style']);
    for (const element of [svg, ...svg.querySelectorAll('*')]) {
      for (const attribute of element.attributes) {
        if ((attribute.localName === 'href' || attribute.name === 'xlink:href') && attribute.value.startsWith('#')) ids.push(attribute.value.slice(1));
        if (urlAttributes.has(attribute.name)) {
          for (const match of attribute.value.matchAll(urlReference)) ids.push(match[1]);
        }
      }
    }
    for (const id of ids) {
      const resolved = scopedDocument.getElementById(id);
      if (!resolved || !svg.contains(resolved)) throw new Error('localReferenceScopeMismatch');
    }
  };
  const isDocumentScopeValid = (target) => {
    return Boolean(
      target &&
      target.frame &&
      target.frame.isConnected &&
      target.document &&
      target.document.body &&
      target.svg &&
      target.document.body.contains(target.svg)
    );
  };
  const isVisibleGeometryValid = (target) => {
    if (!isDocumentScopeValid(target)) return false;
    const frameRect = target.frame.getBoundingClientRect();
    if (!frameRect || frameRect.width <= 0 || frameRect.height <= 0) return false;
    const svgRect = target.svg.getBoundingClientRect();
    return Boolean(svgRect && svgRect.width > 0 && svgRect.height > 0);
  };
  const mountScopedDocument = async (target, encoded, identity, operation) => {
    const parsedSvg = parseSvg(encoded);
    const blobUrl = URL.createObjectURL(new Blob([frameShell], {type:'text/html'}));
    const frame = document.createElement('iframe');
    frame.className = 'ava-document'; frame.setAttribute('sandbox', 'allow-same-origin');
    frame.style.visibility = 'hidden'; frame.src = blobUrl;
    const loaded = new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback) => {
        if (settled) return;
        settled = true; clearTimeout(timeout); callback();
      };
      const timeout = setTimeout(() => {
        diagnose(operation + '.iframeTimeout', identity);
        finish(() => reject(new Error('documentScopeLoadTimeout')));
      }, 5000);
      frame.addEventListener('load', () => finish(() => { diagnose(operation + '.iframeLoad', identity); resolve(); }), {once:true});
      frame.addEventListener('error', () => finish(() => { diagnose(operation + '.iframeError', identity); reject(new Error('documentScopeLoad')); }), {once:true});
    });
    diagnose(operation + '.iframeCreated', identity);
    const previousFrame = target.frame;
    const previousBlobUrl = target.blobUrl;
    target.host.appendChild(frame);
    try {
      await loaded;
      const scopedDocument = frame.contentDocument;
      if (!scopedDocument || !scopedDocument.body) throw new Error('documentScopeUnavailable');
      const svg = scopedDocument.importNode(parsedSvg, true);
      scopedDocument.body.replaceChildren(svg);
      verifyScopedReferences(scopedDocument, svg);
      if (previousFrame && previousFrame !== frame) previousFrame.remove();
      frame.style.visibility = '';
      if (previousBlobUrl) URL.revokeObjectURL(previousBlobUrl);
      target.frame = frame; target.document = scopedDocument; target.svg = svg; target.blobUrl = blobUrl;
      return svg;
    } catch (error) {
      frame.remove(); URL.revokeObjectURL(blobUrl); throw error;
    }
  };
  const same = (left, right) => left && right &&
    left.generationId === right.generationId && left.svgSha256 === right.svgSha256 &&
    (left.manifestSha256 || null) === (right.manifestSha256 || null);
  const showOnly = (shown) => {
    for (const target of [active, draft, candidate]) target.root.classList.toggle('ava-visible', target === shown);
  };
  const clearLayer = (target) => {
    target.host.replaceChildren();
    if (target.blobUrl) URL.revokeObjectURL(target.blobUrl);
    if (target.overlay) target.overlay.replaceChildren();
    target.identity = null;
    target.runtime = null;
    target.frame = null; target.document = null; target.svg = null; target.blobUrl = null;
    target.root.style.background = 'transparent';
    target.root.removeAttribute('aria-label');
    target.root.classList.remove('ava-visible');
  };
  const installDocument = async (target, payload) => {
    const manifest = JSON.parse(decodeUtf8(payload.manifestBase64));
    const svg = await mountScopedDocument(target, payload.svgBase64, payload.identity, 'candidate');
    target.overlay.replaceChildren();
    target.identity = payload.identity;
    target.root.style.background = manifest.background || 'transparent';
    target.root.setAttribute('aria-label', manifest.title);
    const bindings = new Map();
    let selectedBindingId = null;
    let currentStep = 1;
    let rebuildQueued = false;
    for (const binding of manifest.bindings) {
      const element = scopedElement(target, binding.elementId);
      if (!element || element.getAttribute('data-ava-binding') !== binding.bindingId) throw new Error('bindingElementMismatch');
      bindings.set(binding.bindingId, {binding, element});
      for (const id of [binding.elementId, binding.targetId, ...binding.revealIds]) {
        if (!scopedElement(target, id)) throw new Error('bindingTargetMissing');
      }
    }
    const show = (id) => {
      const node = scopedElement(target, id);
      if (!node) return;
      node.style.display = ''; node.style.visibility = 'visible';
    };
    const hide = (id) => {
      const node = scopedElement(target, id);
      if (node) node.style.display = 'none';
    };
    const report = (binding) => bridge('$bindingHandler', {bindingId:binding.bindingId, generationId:payload.identity.generationId});
    const queueRebuild = () => {
      if (rebuildQueued) return;
      rebuildQueued = true; requestAnimationFrame(rebuild);
    };
    const activate = (record) => {
      const binding = record.binding;
      if (manifest.interactionMode !== 'steps' && selectedBindingId === binding.bindingId) return;
      if (manifest.interactionMode === 'explore') {
        if (selectedBindingId && selectedBindingId !== binding.bindingId) {
          const previous = bindings.get(selectedBindingId);
          if (previous) for (const id of previous.binding.revealIds) hide(id);
        }
        for (const id of binding.revealIds) show(id);
      }
      selectedBindingId = binding.bindingId; report(binding); queueRebuild();
    };
    const stepBindings = [...bindings.values()].filter((record) => Number.isInteger(record.binding.stepIndex)).sort((left,right) => left.binding.stepIndex - right.binding.stepIndex);
    const applyStep = (step, shouldReport) => {
      if (manifest.interactionMode !== 'steps' || step < 1 || step > stepBindings.length) return;
      currentStep = step;
      for (const record of stepBindings) {
        if (record.binding.stepIndex === currentStep) show(record.binding.elementId); else hide(record.binding.elementId);
      }
      selectedBindingId = stepBindings[currentStep - 1].binding.bindingId;
      if (shouldReport) report(stepBindings[currentStep - 1].binding);
      queueRebuild();
    };
    function rebuild() {
      rebuildQueued = false; target.overlay.replaceChildren();
      if (manifest.interactionMode !== 'steps') {
        for (const record of bindings.values()) {
          const rect = record.element.getBoundingClientRect();
          if (![rect.left,rect.top,rect.width,rect.height].every(Number.isFinite)) continue;
          const button = document.createElement('button');
          button.type = 'button'; button.className = 'ava-hit';
          button.setAttribute('aria-label', record.binding.accessibilityLabel);
          const width = Math.max(44, rect.width); const height = Math.max(44, rect.height);
          button.style.left = (rect.left + rect.width / 2 - width / 2) + 'px';
          button.style.top = (rect.top + rect.height / 2 - height / 2) + 'px';
          button.style.width = width + 'px'; button.style.height = height + 'px';
          if (record.binding.bindingId === selectedBindingId) button.classList.add('ava-selected');
          button.addEventListener('click', () => activate(record)); target.overlay.appendChild(button);
        }
      }
      if (manifest.interactionMode === 'steps') {
        const controls = document.createElement('div'); controls.className = 'ava-step-controls';
        const back = document.createElement('button'); back.type = 'button'; back.textContent = 'Back'; back.disabled = currentStep === 1;
        back.addEventListener('click', () => applyStep(currentStep - 1, true));
        const count = document.createElement('span'); count.className = 'ava-step-count'; count.textContent = currentStep + ' / ' + stepBindings.length;
        const next = document.createElement('button'); next.type = 'button'; next.textContent = 'Next'; next.disabled = currentStep === stepBindings.length;
        next.addEventListener('click', () => applyStep(currentStep + 1, true));
        controls.append(back,count,next); target.overlay.appendChild(controls);
      }
    }
    if (manifest.interactionMode === 'steps') applyStep(1, false);
    target.runtime = {svg, manifest, bindings, queueRebuild};
    queueRebuild();
  };
  new ResizeObserver(() => {
    if (active.runtime) active.runtime.queueRebuild();
    if (candidate.runtime) candidate.runtime.queueRebuild();
  }).observe(document.getElementById('ava-root'));
  let operationTail = Promise.resolve();
  const enqueueOperation = (operation) => {
    const next = operationTail.then(operation);
    operationTail = next.catch(() => {});
    return next;
  };
  window.avaCanvas = {
    replaceDraft(payload) {
      diagnose('replaceDraft.entered', payload.identity);
      return enqueueOperation(async () => {
        try {
          diagnose('replaceDraft.operationStarted', payload.identity);
          if (payload.authoritative !== false || payload.interactive !== false) throw new Error('draftAuthority');
          await mountScopedDocument(draft, payload.svgBase64, payload.identity, 'draft');
          draft.identity = payload.identity; draft.runtime = null;
          showOnly(draft);
          requestAnimationFrame(() => requestAnimationFrame(() => {
            if (!isVisibleGeometryValid(draft)) {
              fail('draftViabilityFailed', payload.identity);
              return;
            }
            diagnose('draftPresented.bridgeSent', payload.identity);
            bridge('$draftPresentedHandler', payload.identity);
          }));
          return true;
        } catch (_) { fail('draftReplacementFailed', payload.identity); return false; }
      });
    },
    mountCandidate(payload) {
      diagnose('mountCandidate.entered', payload.identity);
      return enqueueOperation(async () => {
        try {
          diagnose('mountCandidate.operationStarted', payload.identity);
          await installDocument(candidate, payload);
          candidate.root.classList.remove('ava-visible');
          if (!isDocumentScopeValid(candidate)) throw new Error('candidateScopeInvalid');
          requestAnimationFrame(() => requestAnimationFrame(() => {
            if (!isDocumentScopeValid(candidate)) {
              fail('rendererPreparationFailed', payload.identity);
              return;
            }
            diagnose('prepared.bridgeSent', payload.identity);
            bridge('$preparedHandler', payload.identity);
          }));
          return true;
        } catch (_) { fail('rendererPreparationFailed', payload.identity); return false; }
      });
    },
    mountActive(payload, interactive) {
      return enqueueOperation(async () => {
        try {
          await installDocument(active, payload);
          active.overlay.classList.toggle('ava-interactive', interactive === true);
          showOnly(active);
          if (!isDocumentScopeValid(active)) throw new Error('activeScopeInvalid');
          return true;
        } catch (_) { fail('activeMountFailed', payload.identity); return false; }
      });
    },
    presentCandidate(identity) {
      return enqueueOperation(() => {
        if (!same(candidate.identity, identity)) { fail('candidatePresentationIdentityMismatch', identity); return false; }
        showOnly(candidate);
        if (candidate.runtime) candidate.runtime.queueRebuild();
        requestAnimationFrame(() => requestAnimationFrame(() => {
          if (!isVisibleGeometryValid(candidate)) {
            fail('candidatePresentationViabilityFailed', identity);
            return;
          }
          diagnose('presented.bridgeSent', identity);
          bridge('$presentedHandler', identity);
        }));
        return true;
      });
    },
    activateCandidate(identity, interactive) {
      return enqueueOperation(() => {
        if (!same(candidate.identity, identity)) { fail('candidateActivationIdentityMismatch', identity); return false; }
        const previousActive = active; active = candidate; candidate = previousActive;
        clearLayer(candidate);
        active.overlay.classList.toggle('ava-interactive', interactive === true);
        clearLayer(draft);
        showOnly(active);
        if (!isDocumentScopeValid(active)) { fail('activeActivationViabilityFailed', identity); return false; }
        return true;
      });
    },
    setActiveInteractive(generationId, interactive) {
      return enqueueOperation(() => {
        if (!active.identity || active.identity.generationId !== generationId) return false;
        active.overlay.classList.toggle('ava-interactive', interactive === true); return true;
      });
    },
    clearPending(generationId) {
      return enqueueOperation(() => {
        if (draft.identity && draft.identity.generationId === generationId) clearLayer(draft);
        if (candidate.identity && candidate.identity.generationId === generationId) clearLayer(candidate);
        if (active.identity) showOnly(active); return true;
      });
    }
  };
})();
</script></body></html>''';
  }

  String replaceDraftScript(AvaValidatedSvgCanvasDraft draft) => _operation(
        'replaceDraft',
        {
          'identity': {
            'generationId': draft.descriptor.generationId,
            'draftIndex': draft.descriptor.draftIndex,
            'svgSha256': draft.descriptor.svgSha256,
          },
          'svgBase64': base64Encode(draft.svgBytes),
          'authoritative': false,
          'interactive': false,
        },
      );

  String mountCandidateScript(AvaValidatedSvgCanvasDocument document) =>
      _operation('mountCandidate', _documentPayload(document));

  String mountActiveScript(
    AvaValidatedSvgCanvasDocument document, {
    required bool interactive,
  }) =>
      'window.avaCanvas.mountActive('
      '${jsonEncode(_documentPayload(document))},${jsonEncode(interactive)});';

  String presentCandidateScript(AvaValidatedSvgCanvasDocument document) =>
      _operation('presentCandidate', _identity(document));

  String activateCandidateScript(
    AvaValidatedSvgCanvasDocument document, {
    required bool interactive,
  }) =>
      'window.avaCanvas.activateCandidate('
      '${jsonEncode(_identity(document))},${jsonEncode(interactive)});';

  String setActiveInteractiveScript({
    required String generationId,
    required bool interactive,
  }) =>
      'window.avaCanvas.setActiveInteractive('
      '${jsonEncode(generationId)},${jsonEncode(interactive)});';

  String clearPendingScript(String generationId) =>
      'window.avaCanvas.clearPending(${jsonEncode(generationId)});';

  String _operation(String name, Map<String, Object?> payload) =>
      'window.avaCanvas.$name(${jsonEncode(payload)});';

  Map<String, Object?> _documentPayload(
    AvaValidatedSvgCanvasDocument document,
  ) =>
      {
        'identity': _identity(document),
        'svgBase64': base64Encode(document.svgBytes),
        'manifestBase64': base64Encode(document.manifestBytes),
      };

  Map<String, Object?> _identity(AvaValidatedSvgCanvasDocument document) => {
        'generationId': document.descriptor.generationId,
        'svgSha256': document.descriptor.svgSha256,
        'manifestSha256': document.descriptor.manifestSha256,
      };
}
