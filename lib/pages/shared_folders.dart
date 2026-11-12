import 'package:drift/drift.dart';
import 'package:filesync/dialogs/folder_prompt.dart';
import 'package:filesync/providers/database_provider.dart';
import 'package:filesync/providers/shared_folders_provider.dart';
import 'package:filesync/services/sync_service.dart';
import 'package:filesync/utils/open_folder.dart';
import 'package:filesync/widgets/add_icon.dart';
import 'package:filesync/widgets/loading_card.dart';
import 'package:filesync/widgets/shared_folder_widget.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class SharedFoldersPageWidget extends ConsumerStatefulWidget {
  const SharedFoldersPageWidget({super.key});

  @override
  ConsumerState<SharedFoldersPageWidget> createState() =>
      _SharedFoldersPageWidgetState();
}

class _SharedFoldersPageWidgetState
    extends ConsumerState<SharedFoldersPageWidget> {
  final Set<String> _selectedFolderIds = {};

  void _updateSelectedFolder(String folderId, bool? add) {
    setState(() {
      if (add == true) {
        _selectedFolderIds.add(folderId);
      } else {
        _selectedFolderIds.remove(folderId);
      }
    });
  }

  Future<void> _view(String folderId) async {
    final db = ref.read(databaseProvider);
    final folder = await db.managers.sharedFolders
        .filter((f) => f.id.equals(folderId))
        .getSingle();
    openFolder(folder.path);
  }

  Future<void> _edit(String folderId) async {
    final db = ref.read(databaseProvider);
    final folder = await db.managers.sharedFolders
        .filter((f) => f.id.equals(folderId))
        .getSingle();

    if (!mounted) return;

    final result = await FolderPromptDialog.prompt(
      context,
      'Edit Folder',
      initialName: folder.name,
      initialPath: folder.path,
    );
    if (result != null && result.$1.isNotEmpty && result.$2.isNotEmpty) {
      final hasPermission = await requestPermissions();
      if (!hasPermission) return;
      await db.managers.sharedFolders
          .filter((f) => f.id.equals(folder.id))
          .update((f) => f(name: Value(result.$1), path: Value(result.$2)));
    }
  }

  Future<void> _delete(String folderId) async {
    final db = ref.read(databaseProvider);
    await db.managers.sharedFolders
        .filter((k) => k.id.equals(folderId))
        .delete();
    setState(() => _selectedFolderIds.remove(folderId));
  }

  Future<void> _deleteSelected() async {
    final db = ref.read(databaseProvider);
    await db.managers.sharedFolders
        .filter((k) => k.id.isIn(_selectedFolderIds))
        .delete();
    setState(() => _selectedFolderIds.clear());
  }

  @override
  Widget build(BuildContext context) {
    final foldersAsync = ref.watch(sharedFoldersProvider);
    final folders = foldersAsync.value;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Shared Folders'),
        actions: [const AddIcon()],
      ),
      body: folders == null
          ? const LoadingCard(text: 'Loading Shared Folders...')
          : Center(
              child: ListView(
                padding: const EdgeInsets.all(10),
                shrinkWrap: folders.isEmpty,
                children: [
                  if (folders.isEmpty)
                    const Text(
                      'Currently not sharing any folder.',
                      textAlign: TextAlign.center,
                      style: TextStyle(fontStyle: FontStyle.italic),
                    )
                  else
                    for (final folder in folders)
                      SharedFolderWidget(
                        folder: folder,
                        isSelected: _selectedFolderIds.contains(folder.id),
                        onChanged: (value) =>
                            _updateSelectedFolder(folder.id, value),
                        onView: () => _view(folder.id),
                        onEdit: () => _edit(folder.id),
                        onDelete: () => _delete(folder.id),
                      ),
                ],
              ),
            ),
      persistentFooterButtons: [
        OutlinedButton.icon(
          icon: const Icon(Icons.remove),
          onPressed: _selectedFolderIds.isEmpty ? null : _deleteSelected,
          label: const Text('Delete'),
          style: OutlinedButton.styleFrom(
            foregroundColor: Colors.red,
            side: _selectedFolderIds.isEmpty
                ? null
                : const BorderSide(color: Colors.red),
          ),
        ),
      ],
    );
  }
}
