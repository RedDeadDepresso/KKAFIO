import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/services.dart';

/// A dialog that allows to prompt for a type to discover on the network.
class FolderPromptDialog extends StatefulWidget {
  final String title;
  final String? initialName;
  final String? initialPath;

  /// Creates a new discovery prompt dialog.
  const FolderPromptDialog({
    super.key,
    required this.title,
    this.initialName,
    this.initialPath,
  });

  @override
  State<StatefulWidget> createState() => _FolderPromptDialogState();

  /// Prompts for a type to discover on the network.
  static Future<(String, String)?> prompt(
    BuildContext context,
    String title, {
    String? initialName,
    String? initialPath,
  }) => showDialog(
    context: context,
    builder: (context) => FolderPromptDialog(
      title: title,
      initialName: initialName,
      initialPath: initialPath,
    ),
  );
}

/// The dialog state.
class _FolderPromptDialogState extends State<FolderPromptDialog> {
  late TextEditingController name;
  late TextEditingController path;

  bool get isValid => name.text.isNotEmpty && path.text.isNotEmpty;

  @override
  void initState() {
    super.initState();
    name = TextEditingController(text: widget.initialName ?? '');
    path = TextEditingController(text: widget.initialPath ?? '');

    name.addListener(() => setState(() {}));
    path.addListener(() => setState(() {}));
  }

  void _selectFolder() async {
    try {
      String? pickedDirectoryPath = await FilePicker.platform.getDirectoryPath(
        dialogTitle: widget.title,
      );
      if (pickedDirectoryPath != null) {
        setState(() {
          path.text = pickedDirectoryPath;
        });
      }
    } on PlatformException catch (e) {
      debugPrint('Unsupported operation: $e');
    } catch (e) {
      debugPrint(e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      scrollable: true,
      title: Text(widget.title),
      content: SizedBox(
        width: MediaQuery.of(context).size.width,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: name,
              decoration: const InputDecoration(labelText: 'Name'),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Flexible(
                  child: TextField(
                    controller: path,
                    decoration: const InputDecoration(labelText: 'Path'),
                    readOnly: true,
                    maxLines: null,
                  ),
                ),
                IconButton(
                  onPressed: _selectFolder,
                  icon: const Icon(Icons.folder),
                ),
              ],
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: isValid
              ? () => Navigator.pop(context, (name.text, path.text))
              : null,
          child: const Text('Ok'),
        ),
      ],
    );
  }

  @override
  void dispose() {
    name.dispose();
    path.dispose();
    super.dispose();
  }
}
