import 'dart:io';
import 'dart:ui';

import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

import '../models/ava_svg_canvas_protocol.dart';

abstract interface class AvaCanvasShareService {
  Future<void> shareSvg({
    required AvaValidatedSvgCanvasDocument document,
    Rect? sharePositionOrigin,
  });
}

typedef AvaTempDirectoryProvider = Future<Directory> Function();
typedef AvaShareInvoker = Future<void> Function(
  List<XFile> files, {
  Rect? sharePositionOrigin,
});

final class IoAvaCanvasShareService implements AvaCanvasShareService {
  const IoAvaCanvasShareService({
    AvaTempDirectoryProvider tempDirectoryProvider = getTemporaryDirectory,
    AvaShareInvoker shareInvoker = _defaultShareInvoker,
  })  : _tempDirectoryProvider = tempDirectoryProvider,
        _shareInvoker = shareInvoker;

  final AvaTempDirectoryProvider _tempDirectoryProvider;
  final AvaShareInvoker _shareInvoker;

  static Future<void> _defaultShareInvoker(
    List<XFile> files, {
    Rect? sharePositionOrigin,
  }) async {
    await Share.shareXFiles(
      files,
      sharePositionOrigin: sharePositionOrigin,
    );
  }

  static String sanitizeFilename(String title) {
    final cleaned = title
        .replaceAll(RegExp(r'[^\w\s\-]'), '')
        .trim()
        .replaceAll(RegExp(r'\s+'), '_');
    final bounded = cleaned.length > 64 ? cleaned.substring(0, 64) : cleaned;
    return bounded.isEmpty ? 'ava-drawing.svg' : 'ava-$bounded.svg';
  }

  @override
  Future<void> shareSvg({
    required AvaValidatedSvgCanvasDocument document,
    Rect? sharePositionOrigin,
  }) async {
    final tempDir = await _tempDirectoryProvider();
    final exportDir = Directory('${tempDir.path}/ava_exports');
    if (!exportDir.existsSync()) {
      await exportDir.create(recursive: true);
    } else {
      try {
        for (final entity in exportDir.listSync()) {
          if (entity is File) {
            try {
              entity.deleteSync();
            } catch (_) {}
          }
        }
      } catch (_) {}
    }
    final filename = sanitizeFilename(document.manifest.title);
    final file = File('${exportDir.path}/$filename');
    await file.writeAsBytes(document.svgBytes, flush: true);
    try {
      await _shareInvoker(
        [
          XFile(
            file.path,
            mimeType: 'image/svg+xml',
            name: filename,
          ),
        ],
        sharePositionOrigin: sharePositionOrigin,
      );
    } finally {
      if (file.existsSync()) {
        try {
          await file.delete();
        } catch (_) {}
      }
    }
  }
}
