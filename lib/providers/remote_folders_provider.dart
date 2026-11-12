import 'dart:async';

import 'package:background_downloader/background_downloader.dart';
import 'package:drift/drift.dart';
import 'package:file_picker/file_picker.dart';
import 'package:filesync/providers/database_provider.dart';
import 'package:filesync/services/sync_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:filesync/models/remote_folder.dart';

/// Manages the state of remote folders for a single discovered device.
///
/// With [AsyncNotifierProvider.family], Riverpod passes the family argument
/// (the host string) as the [arg] parameter of [build]. It is also accessible
/// via the inherited [arg] getter anywhere in the notifier.
class RemoteFoldersNotifier extends AsyncNotifier<Map<String, RemoteFolder>> {
  final String arg;

  RemoteFoldersNotifier(this.arg);

  @override
  FutureOr<Map<String, RemoteFolder>> build() async {
    FileDownloader().registerCallbacks(
      group: arg,
      taskProgressCallback: _handleDownloadProgress,
      taskStatusCallback: _handleDownloadStatus,
    );

    final db = ref.read(databaseProvider);
    final remoteFolders = await fetchRemoteFolders(arg);
    final syncedFolders = await db.managers.syncedFolders
        .filter((f) => f.id.isIn(remoteFolders.keys))
        .get();

    return {
      for (final entry in remoteFolders.entries)
        entry.key: () {
          final synced = syncedFolders
              .where((s) => s.id == entry.key)
              .firstOrNull;
          return synced != null
              ? entry.value.copyWith(path: synced.path)
              : entry.value;
        }(),
    };
  }

  // Convenience getter so methods below don't have to repeat `arg`.
  String get _host => arg;

  Future<void> refresh() async {
    final current = state.value ?? {};
    final remoteFolders = await fetchRemoteFolders(_host);
    state = AsyncData({
      for (final entry in remoteFolders.entries)
        entry.key: () {
          final old = current[entry.key];
          if (old == null) return entry.value;
          return entry.value.copyWith(
            status: old.status,
            downloadProgress: old.downloadProgress,
            path: old.path,
          );
        }(),
    });
  }

  RemoteFolder? getRemoteFolder(String folderId) => state.value?[folderId];

  Future<void> link(String folderId) async {
    final remoteFolder = getRemoteFolder(folderId);
    if (remoteFolder == null) return;
    try {
      final pickedPath = await FilePicker.platform.getDirectoryPath(
        dialogTitle: 'Set local path',
      );
      if (pickedPath == null) return;

      final db = ref.read(databaseProvider);
      if (remoteFolder.path.isEmpty) {
        await db.managers.syncedFolders.create(
          (f) => f(id: remoteFolder.id, path: pickedPath),
        );
      } else {
        await db.managers.syncedFolders
            .filter((f) => f.id.equals(remoteFolder.id))
            .update((f) => f(path: Value(pickedPath)));
      }
      _updateFolder(folderId, (f) => f.copyWith(path: pickedPath));
    } on PlatformException {
      // User cancelled or picker unavailable.
    }
  }

  Future<void> unlink(String folderId) async {
    final db = ref.read(databaseProvider);
    await db.managers.syncedFolders
        .filter((f) => f.id.equals(folderId))
        .delete();
    _updateFolder(folderId, (f) => f.copyWith(path: ''));
  }

  Future<void> unlinkMulti(Iterable<String> folderIds) async {
    final ids = folderIds.toList();
    final db = ref.read(databaseProvider);
    await db.managers.syncedFolders.filter((f) => f.id.isIn(ids)).delete();
    for (final id in ids) {
      _updateFolder(id, (f) => f.copyWith(path: ''));
    }
  }

  Future<void> sync(String folderId) async {
    _updateFolder(
      folderId,
      (f) => f.copyWith(status: RemoteFolderStatus.enqueued),
    );
    await _enqueueSyncTask(folderId);
  }

  Future<void> syncMulti(Iterable<String> folderIds) async {
    final ids = folderIds.toList();
    for (final id in ids) {
      _updateFolder(id, (f) => f.copyWith(status: RemoteFolderStatus.enqueued));
    }
    for (final id in ids) {
      await _enqueueSyncTask(id);
    }
  }

  Future<void> _enqueueSyncTask(String folderId) async {
    final folder = getRemoteFolder(folderId);
    if (folder == null || folder.path.isEmpty) return;

    final hasPermission = await requestPermissions();
    if (!hasPermission) {
      _updateFolder(
        folderId,
        (f) => f.copyWith(status: RemoteFolderStatus.failed),
      );
      return;
    }

    final enqueued = await syncRemoteFolder(_host, folder);
    if (!enqueued) {
      _updateFolder(
        folderId,
        (f) => f.copyWith(status: RemoteFolderStatus.failed),
      );
    }
  }

  void _handleDownloadProgress(TaskProgressUpdate update) {
    _updateFolder(
      update.task.metaData,
      (f) => f.copyWith(
        status: RemoteFolderStatus.downloading,
        downloadProgress: update.progress,
      ),
    );
  }

  void _handleDownloadStatus(TaskStatusUpdate update) {
    final folderId = update.task.metaData;
    final folder = getRemoteFolder(folderId);
    if (folder == null) return;

    if (update.status == TaskStatus.complete) {
      _updateFolder(
        folderId,
        (f) => f.copyWith(status: RemoteFolderStatus.extracting),
      );
      _runExtraction(folder, update.task);
    } else if (update.status == TaskStatus.canceled ||
        update.status == TaskStatus.failed ||
        update.status == TaskStatus.notFound) {
      _updateFolder(
        folderId,
        (f) => f.copyWith(status: RemoteFolderStatus.failed),
      );
    }
  }

  Future<void> _runExtraction(RemoteFolder folder, Task task) async {
    final zipPath = await task.filePath();
    final success = await extractZipFile(zipPath, folder.path);
    _updateFolder(
      folder.id,
      (f) => f.copyWith(
        status: success ? RemoteFolderStatus.synced : RemoteFolderStatus.failed,
      ),
    );
  }

  void _updateFolder(
    String folderId,
    RemoteFolder Function(RemoteFolder) update,
  ) {
    final current = state.value;
    if (current == null) return;
    final folder = current[folderId];
    if (folder == null) return;
    state = AsyncData(Map.of(current)..[folderId] = update(folder));
  }
}

final remoteFoldersProvider =
    AsyncNotifierProvider.family<
      RemoteFoldersNotifier,
      Map<String, RemoteFolder>,
      String
    >(RemoteFoldersNotifier.new);
