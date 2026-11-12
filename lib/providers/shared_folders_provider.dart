import 'package:filesync/providers/database_provider.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

final sharedFoldersProvider = StreamProvider((ref) {
  final db = ref.watch(databaseProvider);
  return db.managers.sharedFolders.watch();
});
