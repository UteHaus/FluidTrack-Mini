# FluidTrack-Mini v0.1.0

First release with standalone builds for Linux and Windows.

## New
- **Desktop window** replaces the tray icon. It shows the runner and Nextcloud status, a live log, and buttons to pause the runner and log in to Nextcloud.
- **Erase after sync** can be switched on with a checkbox in the window. It asks for confirmation first.
- **Windows support:** the key is now detected directly over USB, and libusb is bundled. The adapter needs the WinUSB driver (see README).
- **Standalone builds** for Linux and Windows are built by GitHub Actions and attached to each release.
- **Makefile** with shortcuts for running, building, testing, and backups. Run `make` to see them all.

## Fixed
- **Nextcloud sync didn't work** because of a wrong API address. The table and its columns are now created automatically, and a missing table ID is resolved on its own.
- **Records were marked as synced even when the upload failed.** On every start the app now compares the local database with Nextcloud and uploads anything missing, without duplicates.
- **Match ROM sent the key's ID in the wrong byte order.** Because of this, erasing a key always failed.
- **Unstable USB connection:** every second connection could time out, and the adapter was not released after use.

## Upgrade notes
- The Nextcloud table gets a new **Hash** column automatically. Existing local records are uploaded on the first start.
- The built app reads `.env` and `FluidTrack.db` **next to the executable**. Copy `env.example` to `.env` there.
- On Windows, PIUSI SelfService can't use the adapter while the WinUSB driver is installed.

## Known limitations
- Windows support has not been tested with real hardware yet.
- Erasing keys has not been tested with real hardware yet. Try it with a non-critical key first.
