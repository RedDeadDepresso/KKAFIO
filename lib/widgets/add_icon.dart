import 'package:filesync/dialogs/folder_prompt.dart';
import 'package:filesync/providers/database_provider.dart';
import 'package:filesync/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class AddIcon extends ConsumerWidget {
  const AddIcon({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) => IconButton(
    onPressed: () async {
      (String, String)? result = await FolderPromptDialog.prompt(
        context,
        'Add a shared folder',
      );
      if (result != null && result.$1 != '' && result.$2 != '') {
        final hasPermission = await requestPermissions();
        if (!hasPermission) return;
        final db = ref.read(databaseProvider);
        db.managers.sharedFolders.create(
          (f) => f(name: result.$1, path: result.$2),
        );
      }
    },
    icon: const Icon(Icons.add),
    tooltip: 'Add folder',
  );
}
