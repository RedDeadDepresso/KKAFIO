// This creates a single instance of your database
import 'package:filesync/models/database.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

final databaseProvider = Provider<AppDatabase>((ref) {
  final db = AppDatabase();
  // Clean up the database when the provider is destroyed
  ref.onDispose(() => db.close());

  return db;
});
