import 'package:bonsoir/bonsoir.dart';
import 'package:filesync/constants.dart';
import 'package:filesync/models/discovery.dart';
import 'package:filesync/router/router.dart';
import 'package:filesync/widgets/service_widget.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

/// Displays the current discoveries.
class NearbyDevicesPageWidget extends ConsumerWidget {
  /// Creates a new discoveries page widget instance.
  const NearbyDevicesPageWidget({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('Nearby Devices')),
      body: Padding(
        padding: const EdgeInsets.all(10),
        child: _DiscoveryTypeWidget(type: AppConstants.serviceType),
      ),
    );
  }
}

/// Displays a discovery type.
class _DiscoveryTypeWidget extends ConsumerStatefulWidget {
  /// The type.
  final String type;

  /// Creates a new discovery type widget instance.
  const _DiscoveryTypeWidget({required this.type});

  @override
  ConsumerState<ConsumerStatefulWidget> createState() =>
      _DiscoveryTypeWidgetState();
}

/// The discovery type widget state.
class _DiscoveryTypeWidgetState extends ConsumerState<_DiscoveryTypeWidget>
    with AutomaticKeepAliveClientMixin<_DiscoveryTypeWidget> {
  @override
  bool wantKeepAlive = true;

  @override
  void initState() {
    super.initState();
    ref.listenManual(discoveryTypeStateProvider(widget.type), (_, next) {
      switch (next.value) {
        case BonsoirDiscoveryReadyState():
        case BonsoirDiscoveryStartedState():
          wantKeepAlive = true;
          updateKeepAlive();
          break;
        case BonsoirDiscoveryStoppedState():
        case null:
          wantKeepAlive = false;
          updateKeepAlive();
          break;
      }
    }, fireImmediately: true);
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    AsyncValue<BonsoirDiscoveryState> discoveryState = ref.watch(
      discoveryTypeStateProvider(widget.type),
    );
    if (discoveryState.isLoading) {
      return const Padding(
        padding: EdgeInsets.all(10),
        child: CircularProgressIndicator(),
      );
    } else if (!discoveryState.hasValue ||
        discoveryState.value!.services.isEmpty) {
      return Center(
        child: Card(
          child: ListTile(
            contentPadding: const EdgeInsets.all(10),
            leading: const CircularProgressIndicator(),
            title: Text('Searching for devices nearby...'),
          ),
        ),
      );
    }
    return ListView(
      children: [
        for (BonsoirService service in discoveryState.value!.services)
          ServiceWidget(
            service: service,
            onTap: () =>
                context.push(Routes.nestedNearbyDevice, extra: service),
          ),
      ],
    );
  }
}
