import 'dart:io';

import 'package:bonsoir/bonsoir.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:filesync/constants.dart';
import 'package:flutter_udid/flutter_udid.dart';

/// Manages the mDNS broadcast of this device's FileSync service.
class AppBroadcastService {
  static late BonsoirService _service;
  static late BonsoirBroadcast _broadcast;

  /// The resolved [BonsoirService] for this device.
  static BonsoirService get service => _service;

  /// The active [BonsoirBroadcast] instance.
  static BonsoirBroadcast get broadcast => _broadcast;

  /// Initialises the service and starts broadcasting on the local network.
  static Future<void> initialize() async {
    final deviceInfo = DeviceInfoPlugin();
    final String name;
    final String os;

    if (Platform.isAndroid) {
      name = (await deviceInfo.androidInfo).model;
      os = 'Android';
    } else if (Platform.isIOS) {
      name = (await deviceInfo.iosInfo).localizedModel;
      os = 'iOS';
    } else if (Platform.isMacOS) {
      name = (await deviceInfo.macOsInfo).computerName;
      os = 'macOS';
    } else if (Platform.isWindows) {
      name = (await deviceInfo.windowsInfo).computerName;
      os = 'Windows';
    } else if (Platform.isLinux) {
      name = (await deviceInfo.linuxInfo).name;
      os = 'Linux';
    } else {
      name = 'Flutter';
      os = 'Unknown';
    }

    _service = BonsoirService(
      name: name,
      type: AppConstants.serviceType,
      port: AppConstants.serverPort,
      attributes: {
        AppConstants.attributeOs: os,
        AppConstants.attributeUuid: await FlutterUdid.udid,
      },
    );

    _broadcast = BonsoirBroadcast(service: _service);
    await _broadcast.initialize();
    await _broadcast.start();
  }
}
