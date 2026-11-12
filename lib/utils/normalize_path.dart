import 'package:path/path.dart' as p;

/// Normalize path to be OS-independent and safe for ZIP entries
String normalizePath(String path) {
  // Replace backslashes with forward slashes (ZIP standard)
  path = path.replaceAll(r'\', '/');

  // Remove leading slashes to avoid writing outside base
  path = path.replaceAll(RegExp(r'^/+'), '');

  // Normalize relative separators
  return p.posix.normalize(path);
}
