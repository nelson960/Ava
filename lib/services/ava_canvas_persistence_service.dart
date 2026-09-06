import 'dart:async';
import 'dart:developer' as developer;

import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';

import '../canvas/ava_svg_canvas_session_host.dart';
import '../models/ava_svg_canvas_protocol.dart';
import 'memory_service.dart';

final class AvaCanvasPersistenceConflict implements Exception {
  const AvaCanvasPersistenceConflict(this.message);

  final String message;

  @override
  String toString() => 'AvaCanvasPersistenceConflict: $message';
}

/// Serializes the bounded local Canvas workspace into the existing Drift DB.
final class AvaCanvasPersistenceRepository
    implements AvaStructuralCanvasPersistence {
  AvaCanvasPersistenceRepository(this._database);

  final AppDatabase _database;
  Future<void> _tail = Future<void>.value();
  int _generationEpoch = 0;

  Future<void> get idle => _tail;

  @override
  Future<void> persistActive(AvaValidatedSvgCanvasDocument document,
          {bool focused = true}) =>
      _persistActive(document, focused: focused);

  @override
  Future<void> persistStructuralActive(
    AvaValidatedSvgCanvasDocument document, {
    required bool focused,
    required int workspaceEpoch,
    required int workspaceRevision,
    required int creationSequence,
    required int modificationSequence,
    String? evictedSceneId,
  }) =>
      _persistActive(
        document,
        focused: focused,
        workspaceEpoch: workspaceEpoch,
        workspaceRevision: workspaceRevision,
        creationSequence: creationSequence,
        modificationSequence: modificationSequence,
        evictedSceneId: evictedSceneId,
      );

  Future<void> _persistActive(
    AvaValidatedSvgCanvasDocument document, {
    required bool focused,
    int? workspaceEpoch,
    int? workspaceRevision,
    int? creationSequence,
    int? modificationSequence,
    String? evictedSceneId,
  }) {
    final capturedEpoch = _generationEpoch;
    return _serialize(() async {
      if (capturedEpoch != _generationEpoch) return;
      await _database.transaction(() async {
        final rows = await (_database.select(_database.canvasScenes)
              ..orderBy([
                (row) => OrderingTerm.asc(row.workspaceOrder),
                (row) => OrderingTerm.asc(row.sceneId),
              ]))
            .get();
        final descriptor = document.descriptor;
        final existing =
            rows.where((row) => row.sceneId == descriptor.sceneId).firstOrNull;
        final focusedSceneId = focused
            ? descriptor.sceneId
            : rows.where((row) => row.isFocused).firstOrNull?.sceneId;
        final structural = workspaceEpoch != null;
        if (structural &&
            (workspaceRevision == null ||
                creationSequence == null ||
                modificationSequence == null)) {
          throw const AvaCanvasPersistenceConflict(
            'Canvas structural persistence metadata must be atomic.',
          );
        }
        final expectedEvicted = existing == null &&
                rows.length == AvaSvgCanvasSessionHost.maximumSceneCount
            ? rows.first.sceneId
            : null;
        if (structural && evictedSceneId != expectedEvicted) {
          throw const AvaCanvasPersistenceConflict(
            'Canvas structural persistence eviction does not match durable order.',
          );
        }

        if (existing != null) {
          if (descriptor.sceneRevision < existing.sceneRevision) {
            throw const AvaCanvasPersistenceConflict(
              'A stale Canvas revision cannot replace durable state.',
            );
          }
          if (descriptor.sceneRevision == existing.sceneRevision) {
            if (!_sameIdentity(existing, document)) {
              throw const AvaCanvasPersistenceConflict(
                'A Canvas revision cannot change durable identity.',
              );
            }
          } else {
            await (_database.update(_database.canvasScenes)
                  ..where((row) => row.sceneId.equals(descriptor.sceneId)))
                .write(
              _companion(
                document,
                workspaceOrder: existing.workspaceOrder,
                isFocused: focused || existing.isFocused,
                creationSequence: creationSequence ?? existing.creationSequence,
                modificationSequence:
                    modificationSequence ?? existing.modificationSequence,
              ),
            );
          }
        } else {
          final workspaceOrder = rows.isEmpty
              ? 0
              : rows
                      .map((row) => row.workspaceOrder)
                      .reduce((left, right) => left > right ? left : right) +
                  1;
          await _database.into(_database.canvasScenes).insert(
                _companion(
                  document,
                  workspaceOrder: workspaceOrder,
                  isFocused: focused,
                  creationSequence: creationSequence ?? workspaceOrder + 1,
                  modificationSequence: modificationSequence,
                ),
              );
        }

        if (focusedSceneId != null) await _setSingleFocus(focusedSceneId);
        final retained = await (_database.select(_database.canvasScenes)
              ..orderBy([
                (row) => OrderingTerm.asc(row.workspaceOrder),
                (row) => OrderingTerm.asc(row.sceneId),
              ]))
            .get();
        final excess =
            retained.length - AvaSvgCanvasSessionHost.maximumSceneCount;
        for (final row in retained.take(excess > 0 ? excess : 0)) {
          await (_database.delete(_database.canvasScenes)
                ..where((table) => table.sceneId.equals(row.sceneId)))
              .go();
        }
        final normalized = await (_database.select(_database.canvasScenes)
              ..orderBy([
                (row) => OrderingTerm.asc(row.workspaceOrder),
                (row) => OrderingTerm.asc(row.sceneId),
              ]))
            .get();
        for (var index = 0; index < normalized.length; index++) {
          final row = normalized[index];
          await (_database.update(_database.canvasScenes)
                ..where((table) => table.sceneId.equals(row.sceneId)))
              .write(
            CanvasScenesCompanion(
              workspaceOrder: Value(index),
              isFocused: Value(row.sceneId == focusedSceneId),
            ),
          );
        }
        if (structural) {
          final durableWorkspace =
              await (_database.select(_database.canvasWorkspaces)
                    ..where((row) => row.id.equals(1)))
                  .getSingleOrNull();
          final currentEpoch = durableWorkspace?.workspaceEpoch ?? 0;
          final currentRevision = durableWorkspace?.workspaceRevision ?? 0;
          final durableNextCreation =
              durableWorkspace?.nextCreationSequence ?? 1;
          final durableNextModification =
              durableWorkspace?.nextModificationSequence ?? 1;
          if (workspaceEpoch != currentEpoch ||
              workspaceRevision != currentRevision + 1) {
            throw const AvaCanvasPersistenceConflict(
              'Canvas structural persistence revision is stale or has a gap.',
            );
          }
          await _database
              .into(_database.canvasWorkspaces)
              .insertOnConflictUpdate(
                CanvasWorkspacesCompanion.insert(
                  id: const Value(1),
                  workspaceEpoch: workspaceEpoch,
                  workspaceRevision: workspaceRevision!,
                  nextCreationSequence:
                      durableNextCreation > creationSequence! + 1
                          ? durableNextCreation
                          : creationSequence + 1,
                  nextModificationSequence:
                      durableNextModification > modificationSequence! + 1
                          ? durableNextModification
                          : modificationSequence + 1,
                ),
              );
        }
      });
    });
  }

  @override
  Future<AvaRestoredCanvasWorkspace> loadWorkspace() => _serialize(() async {
        return _database.transaction(() async {
          final rows = await (_database.select(_database.canvasScenes)
                ..orderBy([
                  (row) => OrderingTerm.asc(row.workspaceOrder),
                  (row) => OrderingTerm.asc(row.sceneId),
                ])
                ..limit(AvaSvgCanvasSessionHost.maximumSceneCount))
              .get();
          final documents = <AvaValidatedSvgCanvasDocument>[];
          final creationSequences = <String, int>{};
          final modificationSequences = <String, int?>{};
          final focusedCandidates = <String>[];
          for (final row in rows) {
            try {
              if (row.rendererCapabilityVersion !=
                      avaSvgRendererCapabilityVersion ||
                  row.validatorVersion != avaSvgValidatorVersion ||
                  row.manifestVersion != avaSvgManifestVersion) {
                throw const FormatException(
                  'Persisted Canvas contract version is unsupported.',
                );
              }
              final descriptor = AvaSvgCanvasDocumentDescriptorV4.restored(
                generationId: row.generationId,
                sourceTurnId: row.sourceTurnId,
                sceneId: row.sceneId,
                sceneRevision: row.sceneRevision,
                svgSha256: row.svgSha256,
                manifestSha256: row.manifestSha256,
                svgByteLength: row.svgByteLength,
                manifestByteLength: row.manifestByteLength,
              );
              final document = AvaValidatedSvgCanvasDocument.verify(
                descriptor: descriptor,
                svgBytes: Uint8List.fromList(row.svgBytes),
                manifestBytes: Uint8List.fromList(row.manifestBytes),
              );
              documents.add(document);
              creationSequences[row.sceneId] =
                  row.creationSequence ?? row.workspaceOrder + 1;
              modificationSequences[row.sceneId] = row.modificationSequence;
              if (row.isFocused) focusedCandidates.add(row.sceneId);
            } on Object catch (error, stackTrace) {
              await (_database.delete(_database.canvasScenes)
                    ..where((table) => table.sceneId.equals(row.sceneId)))
                  .go();
              developer.log(
                'canvas_restore_record_discarded sceneId=${row.sceneId} '
                'sceneRevision=${row.sceneRevision} '
                'failureType=${error.runtimeType}',
                name: 'ava.canvas.persistence',
                error: error,
                stackTrace: stackTrace,
              );
            }
          }
          final focusedSceneId = focusedCandidates.length == 1 &&
                  documents.any(
                    (document) =>
                        document.descriptor.sceneId == focusedCandidates.single,
                  )
              ? focusedCandidates.single
              : documents.lastOrNull?.descriptor.sceneId;
          if (focusedSceneId != null) await _setSingleFocus(focusedSceneId);
          var workspace = await (_database.select(_database.canvasWorkspaces)
                ..where((row) => row.id.equals(1)))
              .getSingleOrNull();
          if (workspace == null) {
            final nextCreation = creationSequences.values.fold<int>(
                  0,
                  (current, value) => value > current ? value : current,
                ) +
                1;
            await _database.into(_database.canvasWorkspaces).insert(
                  CanvasWorkspacesCompanion.insert(
                    id: const Value(1),
                    workspaceEpoch: 0,
                    workspaceRevision: 0,
                    nextCreationSequence: nextCreation,
                    nextModificationSequence: 1,
                  ),
                );
            workspace = await (_database.select(_database.canvasWorkspaces)
                  ..where((row) => row.id.equals(1)))
                .getSingle();
          }
          return AvaRestoredCanvasWorkspace(
            documents: List.unmodifiable(documents),
            focusedSceneId: focusedSceneId,
            workspaceEpoch: workspace.workspaceEpoch,
            workspaceRevision: workspace.workspaceRevision,
            nextCreationSequence: workspace.nextCreationSequence,
            nextModificationSequence: workspace.nextModificationSequence,
            creationSequences: Map.unmodifiable(creationSequences),
            modificationSequences: Map.unmodifiable(modificationSequences),
          );
        });
      });

  @override
  Future<void> persistFocus(String sceneId) {
    final capturedEpoch = _generationEpoch;
    return _serialize(() async {
      if (capturedEpoch != _generationEpoch) return;
      await _database.transaction(() async {
        final target = await (_database.select(_database.canvasScenes)
              ..where((row) => row.sceneId.equals(sceneId)))
            .getSingleOrNull();
        if (target == null) {
          throw const AvaCanvasPersistenceConflict(
            'Target scene does not exist for focus persistence.',
          );
        }
        await _setSingleFocus(sceneId);
      });
    });
  }

  @override
  Future<void> reset() {
    _generationEpoch += 1;
    return _serialize(() async {
      await _database.transaction(() async {
        await _database.delete(_database.canvasScenes).go();
        final workspace = await (_database.select(_database.canvasWorkspaces)
              ..where((row) => row.id.equals(1)))
            .getSingleOrNull();
        await _database.into(_database.canvasWorkspaces).insertOnConflictUpdate(
              CanvasWorkspacesCompanion.insert(
                id: const Value(1),
                workspaceEpoch: (workspace?.workspaceEpoch ?? 0) + 1,
                workspaceRevision: 0,
                nextCreationSequence: 1,
                nextModificationSequence: 1,
              ),
            );
      });
    });
  }

  Future<T> _serialize<T>(Future<T> Function() operation) {
    final previous = _tail;
    final result = () async {
      try {
        await previous;
      } on Object {
        // The prior caller observed its own failure. Serialization continues.
      }
      return operation();
    }();
    _tail = result.then<void>(
      (_) {},
      onError: (Object _, StackTrace __) {},
    );
    return result;
  }

  Future<void> _setSingleFocus(String sceneId) async {
    await _database.update(_database.canvasScenes).write(
          const CanvasScenesCompanion(isFocused: Value(false)),
        );
    await (_database.update(_database.canvasScenes)
          ..where((row) => row.sceneId.equals(sceneId)))
        .write(const CanvasScenesCompanion(isFocused: Value(true)));
  }

  CanvasScenesCompanion _companion(
    AvaValidatedSvgCanvasDocument document, {
    required int workspaceOrder,
    required bool isFocused,
    required int? creationSequence,
    required int? modificationSequence,
  }) {
    final descriptor = document.descriptor;
    return CanvasScenesCompanion.insert(
      sceneId: descriptor.sceneId,
      sceneRevision: descriptor.sceneRevision,
      generationId: descriptor.generationId,
      sourceTurnId: descriptor.sourceTurnId,
      svgBytes: Uint8List.fromList(document.svgBytes),
      manifestBytes: Uint8List.fromList(document.manifestBytes),
      svgSha256: descriptor.svgSha256,
      manifestSha256: descriptor.manifestSha256,
      svgByteLength: descriptor.svgByteLength,
      manifestByteLength: descriptor.manifestByteLength,
      rendererCapabilityVersion: avaSvgRendererCapabilityVersion,
      validatorVersion: avaSvgValidatorVersion,
      manifestVersion: avaSvgManifestVersion,
      workspaceOrder: workspaceOrder,
      isFocused: isFocused,
      creationSequence: Value(creationSequence),
      modificationSequence: Value(modificationSequence),
      title: Value(document.manifest.title),
      summary: Value(document.manifest.description),
    );
  }

  bool _sameIdentity(
    StoredCanvasScene stored,
    AvaValidatedSvgCanvasDocument document,
  ) {
    final descriptor = document.descriptor;
    return stored.generationId == descriptor.generationId &&
        stored.sourceTurnId == descriptor.sourceTurnId &&
        stored.sceneRevision == descriptor.sceneRevision &&
        stored.svgSha256 == descriptor.svgSha256 &&
        stored.manifestSha256 == descriptor.manifestSha256 &&
        stored.svgByteLength == descriptor.svgByteLength &&
        stored.manifestByteLength == descriptor.manifestByteLength &&
        listEquals(stored.svgBytes, document.svgBytes) &&
        listEquals(stored.manifestBytes, document.manifestBytes);
  }
}
