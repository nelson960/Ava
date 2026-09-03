import 'dart:developer' as developer;
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:path/path.dart' as p;

import '../models/ava_svg_canvas_protocol.dart';
import 'ava_canvas_share_service.dart';

abstract interface class AvaCanvasSaveService {
  Future<bool> saveSvg({
    required AvaValidatedSvgCanvasDocument document,
  });
}

typedef AvaSaveFilePicker = Future<String?> Function({
  String? dialogTitle,
  String? fileName,
  String? initialDirectory,
  FileType type,
  List<String>? allowedExtensions,
  Uint8List? bytes,
  bool lockParentWindow,
});

final class IoAvaCanvasSaveService implements AvaCanvasSaveService {
  const IoAvaCanvasSaveService({
    AvaSaveFilePicker saveFilePicker = _defaultSaveFilePicker,
  }) : _saveFilePicker = saveFilePicker;

  final AvaSaveFilePicker _saveFilePicker;

  static Future<String?> _defaultSaveFilePicker({
    String? dialogTitle,
    String? fileName,
    String? initialDirectory,
    FileType type = FileType.any,
    List<String>? allowedExtensions,
    Uint8List? bytes,
    bool lockParentWindow = false,
  }) async {
    return FilePicker.saveFile(
      dialogTitle: dialogTitle,
      fileName: fileName,
      initialDirectory: initialDirectory,
      type: type,
      allowedExtensions: allowedExtensions,
      bytes: bytes,
      lockParentWindow: lockParentWindow,
    );
  }

  @override
  Future<bool> saveSvg({
    required AvaValidatedSvgCanvasDocument document,
  }) async {
    try {
      var filename = IoAvaCanvasShareService.sanitizeFilename(
        document.manifest.title,
      );
      if (!filename.toLowerCase().endsWith('.svg')) {
        filename = '$filename.svg';
      }

      developer.log('canvas_save_picker_started', name: 'ava.canvas.save');
      final path = await _saveFilePicker(
        dialogTitle: 'Save SVG drawing',
        fileName: filename,
        type: FileType.custom,
        allowedExtensions: const ['svg'],
        bytes: document.svgBytes,
      );

      final returnedPathPresent = path != null;
      final returnedPathExtension = path != null ? p.extension(path) : '';
      final documentByteLength = document.svgBytes.length;

      developer.log(
        'canvas_save_picker_returned returnedPathPresent=$returnedPathPresent '
        'returnedPathExtension=$returnedPathExtension '
        'documentByteLength=$documentByteLength',
        name: 'ava.canvas.save',
      );

      if (path == null) {
        developer.log('canvas_save_cancelled', name: 'ava.canvas.save');
        return false;
      }

      var targetPath = path;
      if (!targetPath.toLowerCase().endsWith('.svg')) {
        targetPath = '$targetPath.svg';
      }

      final file = File(targetPath);
      if (!file.existsSync() || file.lengthSync() != document.svgBytes.length) {
        await file.writeAsBytes(document.svgBytes, flush: true);
        final fileExists = await file.exists();
        final resultingFileLength = fileExists ? await file.length() : -1;
        developer.log(
          'canvas_save_write_completed byteLength=${document.svgBytes.length} '
          'fileExists=$fileExists resultingFileLength=$resultingFileLength',
          name: 'ava.canvas.save',
        );
      }

      final exists = await file.exists();
      final len = exists ? await file.length() : -1;
      if (!exists || len != document.svgBytes.length) {
        developer.log(
          'canvas_save_failed errorType=VerificationFailed',
          name: 'ava.canvas.save',
        );
        return false;
      }

      return true;
    } on Object catch (e) {
      developer.log(
        'canvas_save_failed errorType=${e.runtimeType}',
        name: 'ava.canvas.save',
      );
      return false;
    }
  }
}
