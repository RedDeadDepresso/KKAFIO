import 'dart:io';

import 'package:open_file_manager/open_file_manager.dart';

void openFolder(String folderPath) {
  if (Platform.isWindows) {
    Process.run('explorer', [folderPath]);
  } else if (Platform.isMacOS) {
    Process.run('open', [folderPath]);
  } else if (Platform.isLinux) {
    Process.run('xdg-open', [folderPath]);
  } else if (Platform.isAndroid || Platform.isIOS) {
    openFileManager(
      androidConfig: AndroidConfig(
        folderType: AndroidFolderType.other,
        folderPath: folderPath,
      ),
    );
  }
}
