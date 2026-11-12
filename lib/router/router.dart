import 'package:bonsoir/bonsoir.dart';
import 'package:filesync/layout/layout_scaffold.dart';
import 'package:filesync/pages/shared_folders.dart';
import 'package:filesync/pages/nearby_device.dart';
import 'package:filesync/pages/nearby_devices.dart';
import 'package:go_router/go_router.dart';

class Routes {
  // Optional: full paths (for reference/navigation)
  static const String nearbyDevices = '/nearby-devices';
  static const String nearbyDevice = 'nearby-device';
  static const String nestedNearbyDevice = '/nearby-devices/nearby-device';
  static const String sharedFolders = '/shared-folders';
}

final router = GoRouter(
  initialLocation: Routes.nearbyDevices,
  routes: [
    StatefulShellRoute.indexedStack(
      builder: (context, state, navigationShell) =>
          LayoutScaffold(navigationShell: navigationShell),
      branches: [
        // Nearby Devices branch
        StatefulShellBranch(
          routes: [
            GoRoute(
              path: Routes.nearbyDevices,
              builder: (context, state) => const NearbyDevicesPageWidget(),
              routes: [
                GoRoute(
                  path: Routes.nearbyDevice, // relative nested route
                  builder: (context, state) => NearbyDevicePageWidget(
                    service: state.extra as BonsoirService,
                  ),
                ),
              ],
            ),
          ],
        ),

        // Broadcasting Folders branch
        StatefulShellBranch(
          routes: [
            GoRoute(
              path: Routes.sharedFolders,
              builder: (context, state) => const SharedFoldersPageWidget(),
            ),
          ],
        ),
      ],
    ),
  ],
);
