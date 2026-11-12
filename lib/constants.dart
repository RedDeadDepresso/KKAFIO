/// App-wide constants.
class AppConstants {
  AppConstants._();

  /// The mDNS service type used for device discovery and broadcast.
  static const String serviceType = '_filesync._tcp';

  /// The port the local HTTP server listens on.
  static const int serverPort = 4000;

  /// mDNS attribute key for the device OS.
  static const String attributeOs = 'os';

  /// mDNS attribute key for the device UUID.
  static const String attributeUuid = 'uuid';

  /// The SQLite database filename.
  static const String databaseName = 'filesync.sqlite';

  /// The Drift database connection name.
  static const String databaseConnectionName = 'filesync';
}
