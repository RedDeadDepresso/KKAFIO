import 'dart:convert';
import 'dart:io';

import 'package:archive/archive_io.dart';
import 'package:background_downloader/background_downloader.dart';
import 'package:filesync/constants.dart';
import 'package:filesync/models/remote_folder.dart';
import 'package:filesync/utils/normalize_path.dart';
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;
import 'package:permission_handler/permission_handler.dart';

/// Fetches the map of remote folders shared by a device at [host].
///
/// Throws if [host] is null or the request fails.
Future<Map<String, RemoteFolder>> fetchRemoteFolders(String host) async {
  final response = await http
      .get(
        Uri.parse('http://$host:${AppConstants.serverPort}/shared-folders'),
        headers: {'Accept': 'application/json'},
      )
      .timeout(const Duration(seconds: 10));

  if (response.statusCode != 200) {
    throw Exception('Failed to fetch remote folders (${response.statusCode})');
  }

  final rawData = jsonDecode(response.body) as Map<String, dynamic>;
  return {
    for (final e in rawData.entries)
      e.key: RemoteFolder(id: e.key, name: e.value as String),
  };
}

/// Returns the set of relative file paths present under [dirPath].
Future<Set<String>> getLocalFiles(String dirPath) async {
  final dir = Directory(dirPath);
  if (!await dir.exists()) return {};

  final files = <String>{};
  await dir.list(recursive: true, followLinks: false).forEach((entity) {
    if (entity is File) {
      final relative = normalizePath(p.relative(entity.path, from: dirPath));
      files.add(relative);
    }
  });
  return files;
}

/// Requests notification and storage permissions.
///
/// Returns true if storage permission is granted.
Future<bool> requestPermissions() async {
  await Permission.notification.request();
  final status = await Permission.manageExternalStorage.request();

  if (status.isGranted) return true;

  if (status.isPermanentlyDenied) {
    openAppSettings();
  }
  return false;
}

/// Extracts [zipPath] into [destinationDirPath].
///
/// Deletes the zip on success when [deleteOnSuccess] is true.
/// Returns true on success, false on failure.
Future<bool> extractZipFile(
  String zipPath,
  String destinationDirPath, {
  bool deleteOnSuccess = true,
}) async {
  final zipFile = File(zipPath);
  if (!await zipFile.exists()) return false;

  final destinationDir = Directory(destinationDirPath);
  if (!await destinationDir.exists()) {
    await destinationDir.create(recursive: true);
  }

  try {
    final bytes = await zipFile.readAsBytes();
    final archive = ZipDecoder().decodeBytes(bytes);

    for (final file in archive) {
      final filename = normalizePath(file.name);
      final outPath = p.join(destinationDir.path, filename);

      if (file.isFile) {
        final outFile = File(outPath);
        await outFile.create(recursive: true);
        await outFile.writeAsBytes(file.content as List<int>);
      } else {
        await Directory(outPath).create(recursive: true);
      }
    }

    if (deleteOnSuccess) await zipFile.delete();
    return true;
  } catch (e) {
    // Log via the caller; return false so the caller can update state.
    return false;
  }
}

/// Enqueues a background download to sync [remoteFolder] from [host].
///
/// The caller is responsible for requesting permissions before calling this.
Future<bool> syncRemoteFolder(String host, RemoteFolder remoteFolder) async {
  final excludeFiles = await getLocalFiles(remoteFolder.path);
  final filename = '${remoteFolder.id}.zip';

  final task = DownloadTask(
    url:
        'http://$host:${AppConstants.serverPort}/shared-folders/${remoteFolder.id}/sync',
    displayName: remoteFolder.name,
    post: excludeFiles.toList(),
    filename: filename,
    baseDirectory: BaseDirectory.temporary,
    group: host,
    metaData: remoteFolder.id,
    updates: Updates.statusAndProgress,
  );
  return FileDownloader().enqueue(task);
}
