enum RemoteFolderStatus {
  idle(''),
  enqueued('Enqueued'),
  downloading('Downloading'),
  extracting('Extracting'),
  synced('Synced'),
  failed('Failed');

  final String label;

  const RemoteFolderStatus(this.label);

  bool get editable => this == idle || this == synced || this == failed;
}

class RemoteFolder {
  final String id;
  final String name;
  final String path;
  final RemoteFolderStatus status;
  final double downloadProgress;

  const RemoteFolder({
    required this.id,
    required this.name,
    this.path = '',
    this.status = RemoteFolderStatus.idle,
    this.downloadProgress = 0,
  });

  RemoteFolder copyWith({
    String? id,
    String? name,
    String? path,
    RemoteFolderStatus? status,
    double? downloadProgress,
  }) =>
      RemoteFolder(
        id: id ?? this.id,
        name: name ?? this.name,
        path: path ?? this.path,
        status: status ?? this.status,
        downloadProgress: downloadProgress ?? this.downloadProgress,
      );
}
