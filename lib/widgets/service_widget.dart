import 'package:bonsoir/bonsoir.dart';
import 'package:filesync/constants.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class ServiceWidget extends ConsumerWidget {
  final BonsoirService service;
  final void Function()? onTap;

  const ServiceWidget({super.key, required this.service, this.onTap});

  String get os => service.attributes[AppConstants.attributeOs] ?? '';
  String get host => service.host ?? '';
  String get port => service.port.toString();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    IconData iconData = switch (os) {
      'Android' => Icons.android,
      'iOS' || 'macOS' => Icons.apple,
      'Windows' || 'Linux' => Icons.monitor,
      _ => Icons.wifi,
    };
    return Card(
      child: ListTile(
        onTap: onTap,
        leading: Icon(iconData),
        title: Text(service.name),
        subtitle: Text('OS: $os, Host: $host, Port: $port'),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}
