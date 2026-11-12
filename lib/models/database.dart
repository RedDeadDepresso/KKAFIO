import 'package:drift/drift.dart';
import 'package:drift_flutter/drift_flutter.dart';
import 'package:filesync/constants.dart';
import 'package:path_provider/path_provider.dart';
import 'package:uuid/uuid.dart';

part 'database.g.dart';

class SharedFolders extends Table {
  TextColumn get id => text().clientDefault(() => const Uuid().v7())();
  TextColumn get name => text().unique()();
  TextColumn get path => text().unique()();

  @override
  Set<Column> get primaryKey => {id};
}

class SyncedFolders extends Table {
  TextColumn get id => text()();
  TextColumn get path => text()();
  DateTimeColumn get lastSync => dateTime().nullable()();

  @override
  Set<Column> get primaryKey => {id};
}

@DriftDatabase(tables: [SharedFolders, SyncedFolders])
class AppDatabase extends _$AppDatabase {
  AppDatabase([QueryExecutor? executor]) : super(executor ?? _openConnection());

  @override
  int get schemaVersion => 1;

  static QueryExecutor _openConnection() {
    return driftDatabase(
      name: AppConstants.databaseConnectionName,
      native: const DriftNativeOptions(
        databaseDirectory: getApplicationSupportDirectory,
      ),
    );
  }
}
