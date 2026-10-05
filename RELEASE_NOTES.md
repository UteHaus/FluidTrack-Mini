# FluidTrack-Mini v0.2.0

## New
- **Key status in the window:** a red dot while a key is read or erased, green when everything is synced, amber while an upload is pending. *Quit* is disabled while a key is read or records are uploaded.
- **Several users, one table:** all installations use the oldest writable table with the configured name, including tables shared with them. `NEXTCLOUD_SHARE_WITH` shares the table automatically with a group or users (read and create rows only). Records from a previous own table are moved over without duplicates.
- **Windows installer (MSI)** in addition to the ZIP archive.
- **German and English user interface.**

## Changed
- **Erasing a key now works like the PIUSI software:** all record names are blanked and the write index is reset, so the dispenser starts at slot 0 again. Previously the whole record area was overwritten and the write index was left unchanged.
- A key that stays on the reader is read once instead of every 5 seconds.

## Fixed
- **Nextcloud login** works with addresses without `https://`, with `http://` redirects, and with URLs copied from the browser. Errors are shown in the window instead of only in the log.
- **Browser did not open** from the Linux build. If no browser can be started, the login link is shown for copying.
- **Switching to another Nextcloud** could reuse the table ID of the old server and write into a foreign table.
- **Duplicate rows** when two instances ran at the same time: only one instance per database can run now, and records already in the table are not uploaded again.

## Upgrade notes
- Erase keys with a non-critical key first and check that the dispenser accepts it.
- If the source run and the build should share data, set the same absolute `DB_PATH` in both `.env` files.
