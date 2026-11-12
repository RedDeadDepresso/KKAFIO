import 'package:bonsoir/bonsoir.dart';
import 'package:filesync/providers/remote_folders_provider.dart';
import 'package:filesync/utils/open_folder.dart';
import 'package:filesync/widgets/loading_card.dart';
import 'package:filesync/widgets/remote_folder_widget.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class NearbyDevicePageWidget extends ConsumerStatefulWidget {
  final BonsoirService service;

  const NearbyDevicePageWidget({super.key, required this.service});

  @override
  ConsumerState<NearbyDevicePageWidget> createState() =>
      _NearbyDevicePageWidgetState();
}

class _NearbyDevicePageWidgetState
    extends ConsumerState<NearbyDevicePageWidget> {
  final Set<String> _selectedFolderIds = {};

  /// Guard: returns null (and shows a snackbar) when the service host is not
  /// yet resolved. Callers must bail out on null.
  String? get _resolvedHost {
    final h = widget.service.host;
    if (h == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Device not yet resolved. Please wait.')),
      );
    }
    return h;
  }

  RemoteFoldersNotifier? _notifier() {
    final h = _resolvedHost;
    if (h == null) return null;
    return ref.read(remoteFoldersProvider(h).notifier);
  }

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
    final folder = _notifier()?.getRemoteFolder(folderId);
    if (folder == null) return;
    openFolder(folder.path);
  }

  Future<void> _sync(String folderId) async {
    setState(() => _selectedFolderIds.remove(folderId));
    await _notifier()?.sync(folderId);
  }

  Future<void> _unlink(String folderId) async {
    setState(() => _selectedFolderIds.remove(folderId));
    await _notifier()?.unlink(folderId);
  }

  Future<void> _syncSelected() async {
    final ids = {..._selectedFolderIds};
    setState(() => _selectedFolderIds.clear());
    await _notifier()?.syncMulti(ids);
  }

  Future<void> _unlinkSelected() async {
    final ids = {..._selectedFolderIds};
    setState(() => _selectedFolderIds.clear());
    await _notifier()?.unlinkMulti(ids);
  }

  @override
  Widget build(BuildContext context) {
    final host = widget.service.host;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.service.name),
        actions: [
          IconButton(
            onPressed: host == null ? null : () => _notifier()?.refresh(),
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
          ),
        ],
      ),
      body: host == null
          ? const LoadingCard(text: 'Resolving device...')
          : Center(
              child: ref.watch(remoteFoldersProvider(host)).when(
                data: (remoteFolders) => remoteFolders.isEmpty
                    ? const Text(
                        'The device is not sharing any folder.',
                        textAlign: TextAlign.center,
                        style: TextStyle(fontStyle: FontStyle.italic),
                      )
                    : ListView(
                        padding: const EdgeInsets.all(10),
                        children: [
                          for (final remoteFolder in remoteFolders.values)
                            RemoteFolderWidget(
                              remoteFolder: remoteFolder,
                              isSelected:
                                  _selectedFolderIds.contains(remoteFolder.id),
                              onChanged: (add) =>
                                  _updateSelectedFolder(remoteFolder.id, add),
                              onSync: () => _sync(remoteFolder.id),
                              onView: () => _view(remoteFolder.id),
                              onLink: () => _notifier()?.link(remoteFolder.id),
                              onUnlink: () => _unlink(remoteFolder.id),
                            ),
                        ],
                      ),
                error: (err, _) => Text('$err'),
                loading: () =>
                    const LoadingCard(text: 'Loading remote folders...'),
              ),
            ),
      persistentFooterButtons: [
        OutlinedButton.icon(
          icon: const Icon(Icons.remove),
          onPressed: _selectedFolderIds.isEmpty ? null : _unlinkSelected,
          label: const Text('Unlink'),
          style: OutlinedButton.styleFrom(
            foregroundColor: Colors.red,
            side: _selectedFolderIds.isEmpty
                ? null
                : const BorderSide(color: Colors.red),
          ),
        ),
        FilledButton.icon(
          icon: const Icon(Icons.sync),
          onPressed: _selectedFolderIds.isEmpty ? null : _syncSelected,
          label: const Text('Sync'),
        ),
      ],
    );
  }
}
