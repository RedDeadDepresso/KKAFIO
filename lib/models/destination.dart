import 'package:flutter/material.dart';

class Destination {
  final IconData icon;
  final String labelMobile;
  final String labelDesktop;

  const Destination({
    required this.icon,
    required this.labelMobile,
    required this.labelDesktop,
  });
}

const destinations = [
  Destination(
    icon: Icons.smartphone,
    labelMobile: 'Nearby Devices',
    labelDesktop: 'Nearby',
  ),
  Destination(
    icon: Icons.wifi_tethering,
    labelMobile: 'Shared Folders',
    labelDesktop: 'Shared',
  ),
];
