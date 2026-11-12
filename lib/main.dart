import 'package:background_downloader/background_downloader.dart';
import 'package:filesync/router/router.dart';
import 'package:filesync/services/app_broadcast_service.dart';
import 'package:filesync/services/http_server.dart';
import 'package:filesync/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AppBroadcastService.initialize();
  FileDownloader().configureNotification(
    running: const TaskNotification(
      'Download {displayName}',
      'File: {filename} - {progress} - speed {networkSpeed} and {timeRemaining} remaining',
    ),
    complete: const TaskNotification(
      'Download {displayName}',
      'Download complete',
    ),
    error: const TaskNotification('Download {displayName}', 'Download failed'),
    paused: const TaskNotification('Download {displayName}', 'Paused'),
    canceled: const TaskNotification('Download {displayName}', 'Canceled'),
    progressBar: true,
  );
  await startBackgroundServer();
  runApp(const ProviderScope(child: FileSyncApp()));
}

class FileSyncApp extends StatelessWidget {
  const FileSyncApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp.router(
    routerConfig: router,
    theme: AppTheme.light,
    darkTheme: AppTheme.dark,
    themeMode: ThemeMode.system,
  );
}
