# KKAFIO

A cross-platform Flutter app for sharing and syncing folders between nearby devices over a local network — no internet connection or cloud account required.

## How it works

KKAFIO uses mDNS (via [Bonsoir](https://pub.dev/packages/bonsoir)) to automatically discover other devices running the app on the same network. Each device runs a lightweight HTTP server on port 4000 that exposes its shared folders. When you sync, only files you don't already have are downloaded — existing files are sent as an exclusion list so the remote device can zip and serve only what's missing.

```
Device A                            Device B
────────                            ────────
Share folder  ──── mDNS discovery ──▶  See Device A
                                        Browse folders
              ◀─── HTTP sync ─────────  Download new files
```

## Features

- **Automatic device discovery** — finds other KKAFIO devices on your LAN via mDNS, no manual IP entry needed
- **Folder sharing** — choose any local folder to expose to nearby devices
- **Incremental sync** — only missing files are downloaded; existing files are skipped
- **Background downloads** — syncs run in the background with progress notifications
- **Cross-platform** — Android, iOS, Windows, Linux, and macOS
- **No cloud dependency** — everything stays on your local network

## Getting started

### Prerequisites

- [Flutter SDK](https://docs.flutter.dev/get-started/install) ≥ 3.41.0 (Dart SDK `^3.11.1`)
- Devices must be on the same Wi-Fi or local network

### Install dependencies

```bash
flutter pub get
```

### Run code generation (required for Drift database)

```bash
dart run build_runner build
```

### Run the app

```bash
flutter run
```

## Building for release

The repository includes a GitHub Actions workflow (`.github/workflows/build_release.yml`) that builds and publishes releases for Android, iOS, Windows, and Linux.

**Trigger a release** by pushing a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Or trigger a manual build from the Actions tab, selecting your target platforms.

### Required secrets (set in repo Settings → Secrets)

| Secret                         | Description                                    |
| ------------------------------ | ---------------------------------------------- |
| `ANDROID_KEYSTORE_BASE64`      | Base64-encoded JKS/PKCS12 keystore             |
| `ANDROID_KEY_ALIAS`            | Key alias inside the keystore                  |
| `ANDROID_KEY_PASSWORD`         | Key password                                   |
| `ANDROID_STORE_PASSWORD`       | Store password                                 |
| `IOS_CERTIFICATE_BASE64`       | Base64-encoded `.p12` distribution certificate |
| `IOS_CERTIFICATE_PASSWORD`     | `.p12` password                                |
| `IOS_PROVISION_PROFILE_BASE64` | Base64-encoded `.mobileprovision`              |
| `IOS_TEAM_ID`                  | Apple developer team ID (10 characters)        |
| `IOS_BUNDLE_ID`                | e.g. `com.yourcompany.KKAFIO`                  |

## Project structure

```
lib/
├── main.dart                    # App entry point; starts mDNS broadcast and HTTP server
├── constants.dart               # App-wide constants (port, service type, DB name)
├── models/
│   ├── database.dart            # Drift database schema (shared folders table)
│   ├── discovery.dart           # mDNS discovery state machine
│   └── remote_folder.dart       # Remote folder model
├── services/
│   ├── app_broadcast_service.dart  # Registers and broadcasts this device via mDNS
│   ├── http_server.dart            # Shelf HTTP server (runs in a background isolate)
│   └── sync_service.dart           # Sync logic: fetch folder list, diff, download, extract
├── providers/                   # Riverpod providers
├── pages/
│   ├── nearby_devices.dart      # Discover and list nearby KKAFIO devices
│   ├── nearby_device.dart       # Browse a specific device's shared folders
│   └── shared_folders.dart      # Manage your own shared folders
├── widgets/                     # Reusable UI components
├── router/                      # go_router navigation
├── theme/                       # Light and dark themes
└── utils/                       # Path normalisation, sorting, folder opening
```

## Key dependencies

| Package                                                                                             | Purpose                                                 |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| [`bonsoir`](https://pub.dev/packages/bonsoir)                                                       | mDNS device discovery and broadcast                     |
| [`shelf`](https://pub.dev/packages/shelf) + [`shelf_router`](https://pub.dev/packages/shelf_router) | Embedded HTTP server                                    |
| [`drift`](https://pub.dev/packages/drift)                                                           | Local SQLite database for shared folder registry        |
| [`flutter_riverpod`](https://pub.dev/packages/flutter_riverpod)                                     | State management                                        |
| [`background_downloader`](https://pub.dev/packages/background_downloader)                           | Background file downloads with progress notifications   |
| [`archive`](https://pub.dev/packages/archive)                                                       | Zip creation (server side) and extraction (client side) |
| [`go_router`](https://pub.dev/packages/go_router)                                                   | Declarative navigation                                  |

## HTTP API

The embedded server exposes two endpoints on port 4000:

| Method | Path                        | Description                                                               |
| ------ | --------------------------- | ------------------------------------------------------------------------- |
| `GET`  | `/shared-folders`           | Returns `{ id: name }` map of all shared folders                          |
| `POST` | `/shared-folders/<id>/sync` | Streams a zip of the folder, excluding paths provided in the request body |

The sync endpoint accepts an optional JSON array (or object with path keys) of relative file paths that the client already has. These are excluded from the zip, so only new or missing files are transferred.

## Permissions

**Android** requires the following permissions (declared in `AndroidManifest.xml`):

- `INTERNET` — network communication
- `MANAGE_EXTERNAL_STORAGE` — reading and writing files outside app-private storage
- `POST_NOTIFICATIONS` — download progress notifications

On first sync the app will request storage and notification permissions at runtime.