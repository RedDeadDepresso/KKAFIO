import 'package:filesync/models/remote_folder.dart';
import 'package:flutter/material.dart';

/// Allows to display a discovered service.
class RemoteFolderWidget extends StatelessWidget {
  /// The discovered service.
  final RemoteFolder remoteFolder;
  final bool? isSelected;
  final Function(bool?)? onChanged;
  final Function()? onSync;
  final Function()? onLink;
  final Function()? onView;
  final Function()? onUnlink;

  /// Creates a new service widget.
  const RemoteFolderWidget({
    super.key,
    required this.remoteFolder,
    this.isSelected = false,
    this.onChanged,
    this.onSync,
    this.onView,
    this.onLink,
    this.onUnlink,
  });

  @override
  Widget build(BuildContext context) {
    String title = remoteFolder.name;
    if (remoteFolder.status != RemoteFolderStatus.idle) {
      title += ' - ${remoteFolder.status.label}';
    }
    if (remoteFolder.status.editable) {
      return Card(
        child: CheckboxListTile(
          title: Text(title),
          subtitle: Text(remoteFolder.path),
          secondary: remoteFolder.path.isEmpty
              ? TextButton(onPressed: onLink, child: const Text('Link'))
              : PopupMenuButton(
                  itemBuilder: (BuildContext context) => <PopupMenuEntry>[
                    PopupMenuItem(onTap: onSync, child: Text('Sync')),
                    PopupMenuItem(onTap: onView, child: Text('View')),
                    PopupMenuItem(onTap: onLink, child: Text('Link')),
                    PopupMenuItem(
                      onTap: onUnlink,
                      child: Text(
                        'Unlink',
                        style: TextStyle(color: Colors.red),
                      ),
                    ),
                  ],
                ),
          value: isSelected,
          enabled: remoteFolder.path.isNotEmpty,
          controlAffinity: ListTileControlAffinity.leading,
          onChanged: onChanged,
        ),
      );
    }
    return Card(
      child: ListTile(
        leading: remoteFolder.status == RemoteFolderStatus.downloading &&
                remoteFolder.downloadProgress > 0.0 &&
                remoteFolder.downloadProgress < 1.0
            ? CircularProgressIndicator(value: remoteFolder.downloadProgress)
            : const CircularProgressIndicator(),
        title: Text(title),
        subtitle: Text(remoteFolder.path),
      ),
    );
  }
}
