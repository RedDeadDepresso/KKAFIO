import 'package:filesync/models/database.dart';
import 'package:flutter/material.dart';

class SharedFolderWidget extends StatelessWidget {
  final SharedFolder folder;
  final bool? isSelected;
  final Function(bool?)? onChanged;
  final Function()? onView;
  final Function()? onEdit;
  final Function()? onDelete;

  const SharedFolderWidget({
    super.key,
    required this.folder,
    this.isSelected = false,
    this.onView,
    this.onChanged,
    this.onEdit,
    this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      child: CheckboxListTile(
        title: Text(folder.name),
        subtitle: Text(folder.path),
        secondary: PopupMenuButton(
          itemBuilder: (BuildContext context) => <PopupMenuEntry>[
            PopupMenuItem(onTap: onView, child: Text('View')),
            PopupMenuItem(onTap: onEdit, child: Text('Edit')),
            PopupMenuItem(
              onTap: onDelete,
              child: Text('Delete', style: TextStyle(color: Colors.red)),
            ),
          ],
        ),
        value: isSelected,
        enabled: folder.path.isNotEmpty,
        controlAffinity: ListTileControlAffinity.leading,
        onChanged: onChanged,
      ),
    );
  }
}
