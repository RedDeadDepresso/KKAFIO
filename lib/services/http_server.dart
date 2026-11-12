import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:isolate';

import 'package:archive/archive_io.dart';
import 'package:drift/native.dart';
import 'package:filesync/constants.dart';
import 'package:filesync/models/database.dart';
import 'package:filesync/utils/normalize_path.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as shelf_io;
import 'package:shelf_router/shelf_router.dart';

/// Spawns the HTTP server in a background isolate.
Future<void> startBackgroundServer() async {
  final supportDir = await getApplicationSupportDirectory();
  final tempDir = await getApplicationCacheDirectory();
  await Isolate.spawn(_serverMain, [supportDir.path, tempDir.path]);
}

/// Builds a zip of [dirPath] (excluding [excludePaths]) into a temp file,
/// then returns the file's stream and its byte length.
///
/// The caller MUST fully consume the stream — the temp file is deleted
/// automatically once the stream closes (normally or on error).
Future<(Stream<Object?>, int)> buildZip(
  String dirPath,
  Set<String> excludePaths,
  String tempPath,
) async {
  final tempFile = File(
    p.join(tempPath, '${DateTime.now().millisecondsSinceEpoch}.zip'),
  );

  final encoder = ZipFileEncoder();
  encoder.create(tempFile.path);

  final baseDir = Directory(dirPath);
  await for (final entity in baseDir.list(recursive: true)) {
    if (entity is! File) continue;
    final relativePath = normalizePath(p.relative(entity.path, from: dirPath));
    if (excludePaths.contains(relativePath)) continue;
    await encoder.addFile(entity, relativePath);
  }
  await encoder.close();

  final fileSize = await tempFile.length();

  // Stream the file in chunks and delete it once the stream is done.
  final stream = tempFile.openRead().transform(
    StreamTransformer.fromHandlers(
      handleDone: (sink) {
        sink.close();
        tempFile.delete().ignore();
      },
      handleError: (error, stack, sink) {
        sink.addError(error, stack);
        tempFile.delete().ignore();
      },
    ),
  );

  return (stream, fileSize);
}

void _serverMain(List<dynamic> args) async {
  final supportPath = args[0] as String;
  final tempPath = args[1] as String;

  final dbFile = File(p.join(supportPath, AppConstants.databaseName));
  final db = AppDatabase(NativeDatabase(dbFile));

  final app = Router();

  app.get('/shared-folders', (Request request) async {
    final folders = await db.managers.sharedFolders.get();
    final data = {for (final f in folders) f.id: f.name};
    return Response.ok(
      jsonEncode(data),
      headers: {'content-type': 'application/json'},
    );
  });

  app.post('/shared-folders/<id>/sync', (Request request, String id) async {
    try {
      // Validate the folder exists before doing any work.
      final folders = await db.managers.sharedFolders
          .filter((f) => f.id.equals(id))
          .get();

      if (folders.isEmpty) {
        return Response.notFound('Folder not found');
      }
      final folder = folders.first;

      // Parse exclude list; treat any malformed body as empty.
      Set<String> excludePaths = {};
      try {
        final payload = await request.readAsString();
        if (payload.isNotEmpty) {
          final decoded = jsonDecode(payload);
          if (decoded is List) {
            excludePaths = Set<String>.from(decoded.whereType<String>());
          } else if (decoded is Map) {
            excludePaths = Set<String>.from(decoded.keys.whereType<String>());
          }
        }
      } catch (_) {
        // Malformed body — proceed with no exclusions.
      }

      final (zipStream, fileSize) = await buildZip(
        folder.path,
        excludePaths,
        tempPath,
      );

      return Response.ok(
        zipStream,
        headers: {
          'Content-Type': 'application/zip',
          'Content-Disposition': 'attachment; filename="$id.zip"',
          'Content-Length': fileSize.toString(),
        },
      );
    } catch (e, st) {
      // ignore: avoid_print
      print('SERVER ERROR: $e\n$st');
      return Response.internalServerError(body: 'Internal server error');
    }
  });

  await shelf_io.serve(
    app.call,
    InternetAddress.anyIPv4,
    AppConstants.serverPort,
  );
}
