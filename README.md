# FluidTrack-Mini 🔑⛽

A minimalist, modular open-source tool that reads and parses fueling transactions directly from a **PIUSI iButton fuel key (DS1996)** via a USB 1-Wire adapter, and syncs the logs to a local SQLite database and (optionally) Nextcloud Tables. Developed with ⚡ **uv**.

> ℹ️ This project talks **directly to the physical fuel key over USB**, independent of the original PIUSI "SelfService" Windows software. The on-key data format was fully reverse-engineered (see [Protocol Notes](#-protocol-notes--how-the-format-was-determined) below) and cross-validated against real exported transaction records.

## 🏭 Compatible Hardware

Designed for **PIUSI fuel management systems** that identify drivers/vehicles via a red **DS1996 "Master Key"** iButton and manage records through the PIUSI "SelfService" software suite (e.g. **PIUSI CUBE MC** series dispenser installations). It should work with any setup using the same DS1996-based key and a DS9490R USB reader, regardless of which specific PIUSI dispenser model is deployed on-site — the key itself, not the dispenser, is what this tool reads.

## 🚀 Features

* **Direct USB Hardware Access:** Talks to the Maxim/Dallas **DS2490** chip inside the DS9490R adapter directly over USB (`pyusb`), independent of OS-level 1-Wire slave drivers — this matters because mainstream Linux kernels have **no dedicated slave driver for the DS1996 family (`0x0c`)**, so reading through the generic `sysfs` files silently returns blank (`0xFF`) data.
* **Correct Multi-Device Handling:** Reliably distinguishes the DS1996 fuel key (family `0x0c`) from a second, memory-less identification chip (DS1420, family `0x81`) that can be present on the same 1-Wire bus.
* **Full Ring-Buffer Parsing:** Reads and decodes all 255 stored transactions (BCD-encoded date/time, 3-byte BCD liters, vehicle/equipment name) from the key's ring-buffer memory layout.
* **Hash-Based Deduplication:** Every transaction gets a stable content hash, so re-reading the same key (e.g. before it's been synced/erased) never creates duplicate rows locally or in Nextcloud.
* **Nextcloud is Optional:** Runs perfectly fine with local SQLite only. If Nextcloud credentials are configured, it also auto-provisions the target Table (columns: Station, Timestamp, Liters, Operator, KeyID) and syncs new rows.
* **Browser-Based Nextcloud Login:** Optional one-time setup (`nextcloud_login.py`) using Nextcloud's official "Login Flow v2" — the same mechanism the Nextcloud Desktop Client uses. No manual app-password generation required.
* **Optional Key Erasure:** Can automatically wipe the transaction area of the key once *every* record on it is confirmed stored (and, if enabled, synced to Nextcloud) — strictly opt-in via `.env`, off by default.
* **License Plate Formatting:** Converts dot-notation vehicle plates (`AB.CD.1234`) found on the key into standard street notation (`AB-CD 1234`); equipment names that don't match the plate pattern (e.g. `FORKLIFT`, `EXCAVATOR-2`) pass through unchanged.

## 📋 Hardware & Environment Requirements

1. **Data Carrier:** PIUSI iButton fuel key — **DS1996+F5** (64 kbit NV-RAM, family code `0x0c`). The `+F5` suffix is just a Maxim packaging variant; it doesn't change the protocol.
2. **Hardware Interface:** Maxim/Dallas **DS9490R** USB-to-1-Wire adapter (contains the DS2490 USB-to-1-Wire bridge chip).

> 🐧 **Linux notes:**
> - The DS9490R does **not** create a virtual serial port (no `ttyUSB0`/`ttyACM0`) — it's a native USB device.
> - The `ds2490` kernel module (typically auto-loaded, or `sudo modprobe ds2490`) is still used, but **only** to enumerate which 1-Wire ROM IDs are currently on the bus via `/sys/bus/w1/devices/.../w1_master_slaves`. The actual reading/writing of key memory bypasses the kernel driver entirely and talks to the DS2490 directly over USB — this is what makes reading the DS1996 family reliable in the first place.
> - You'll likely need a **udev rule** so the adapter is accessible without `sudo`:
>   ```
>   # /etc/udev/rules.d/99-ds9490.rules
>   SUBSYSTEM=="usb", ATTR{idVendor}=="04fa", ATTR{idProduct}=="2490", MODE="0666"
>   ```
>   Then: `sudo udevadm control --reload-rules && sudo udevadm trigger`, and re-plug the adapter.

> 🪟 **Windows notes:** `pyusb` needs a libusb-compatible driver for the DS9490R (e.g. install one via [Zadig](https://zadig.akeo.ie/), selecting **WinUSB**). The vendor's official TMEX driver, if installed, is *not* usable by `pyusb` directly.

## 🛠️ Repository File Structure

* **`main.py`** — Runtime loop: detects the key's ROM ID, reads its full memory over USB, parses transactions, deduplicates, stores locally, syncs to Nextcloud (if configured), and optionally erases the key.
* **`ds9490_direct.py`** — Low-level DS2490 USB protocol implementation (`pyusb`): reset, byte I/O, block reads, Match ROM addressing, Write/Read/Copy Scratchpad, plus the transaction-record parser (`scan_records`).
* **`database.py`** — Local SQLite schema, migrations, hash-based dedup lookups, and row status tracking.
* **`nextcloud.py`** — OCS-REST-API communication, table auto-provisioning, and row uploads. Fully optional — see below.
* **`nextcloud_login.py`** — One-time interactive setup script: browser-based Nextcloud login (Login Flow v2), writes credentials into `.env` automatically.

## ⚙️ Configuration Setup

### 1. Project Initialization
```bash
git clone https://github.com/your-org/FluidTrack-Mini
cd FluidTrack-Mini
uv sync
```

### 2. Nextcloud Login (optional)
If you want cloud sync, run this once instead of manually creating an app password:
```bash
uv run python nextcloud_login.py https://your-nextcloud-instance.com
```
This opens your browser, lets you log in normally (including 2FA), and writes `NEXTCLOUD_URL`, `NEXTCLOUD_USER`, and `NEXTCLOUD_APP_TOKEN` into `.env` automatically. Skip this step entirely if you only want local SQLite storage.

### 3. Environment Variables (`.env`)
Copy the template and adjust it. All settings are documented inside [`env.example`](env.example).
```bash
cp env.example src/.env
```
When running from source, the app reads `src/.env`. The built app reads the `.env` next to its executable. Every setting is optional. Without Nextcloud credentials the app stores data locally only. `NEXTCLOUD_TABLE_ID` is filled in automatically.

## 💻 Usage Instructions

Plug in the DS9490R, rest the iButton key on the reader socket, and run:
```bash
uv run python main.py
```

### Desktop Window (optional)
Instead of the console loop you can start a small tkinter control window:
```bash
uv run python tray_app.py
```
It shows the runner status and a live log, and lets you pause/resume the runner and log in to Nextcloud. tkinter has no system-tray support, so closing the window minimizes it to the taskbar; use **Quit** to exit. On minimal Linux installs you may need `sudo apt install python3-tk`.

### Makefile Shortcuts
Run `make` to list all targets. The most common ones are `make sync`, `make run`, `make build`, `make cloud-sync` and `make backup-db`. `make build` saves and restores the built app's `.env` and database automatically. PyInstaller cannot cross-compile, so run `make build-windows` on Windows from Git Bash or MSYS2.

### Standalone Build (PyInstaller)
```bash
uv run pyinstaller --noconfirm FluidTrack-Mini.spec
cp src/.env dist/FluidTrack-Mini/.env
```
The built app reads `.env` and stores `FluidTrack.db` **next to the executable** (`dist/FluidTrack-Mini/`), not inside `_internal/`. A rebuild with `--noconfirm` deletes that folder, so back up `.env` and the database before rebuilding.

The window shows the Nextcloud connection state: green means connected with the table ready, red means not connected (reason shown), and grey means not configured. The connection is re-checked every minute. A missing or outdated `NEXTCLOUD_TABLE_ID` is resolved automatically by table title, and the table is created if it doesn't exist.

### Execution Flow
1. `main.py` boots, loads `.env`, and initializes/migrates the local SQLite schema.
2. If Nextcloud is configured, `nextcloud.py` verifies/creates the target Table; otherwise cloud sync is skipped entirely (this is fully supported, not a fallback/error state).
3. Every 5 seconds, the loop checks the 1-Wire bus for a DS1996 (family `0x0c`) ROM ID — ignoring any other device families present (e.g. a DS1420 identification chip).
4. On detection: the full 8192-byte key memory is read directly over USB (`ds9490_direct.py`), all 255 ring-buffer transaction slots are parsed, and each gets a dedup hash.
5. New (not-yet-seen) transactions are inserted locally and, if Nextcloud is configured, uploaded.
6. If `DELETE_KEY_AFTER_SYNC=true` **and** every transaction currently on the key is confirmed fully processed, the transaction area is wiped (verified afterward by re-reading) — the key's header (firmware version, station number) is preserved.

## 📊 Data Mapping Structure

| iButton Field | SQLite Column | Nextcloud Column | Example |
| :--- | :--- | :--- | :--- |
| Station number (16-byte header) | `serial_number` | Station | `"100001"` |
| Date + time (combined, ISO 8601) | `timestamp` | Timestamp | `"2026-01-15T09:30:00"` |
| Liters (3×BCD bytes, ÷100) | `liters` | Liters | `45.80` |
| Vehicle / equipment name (plate-formatted if applicable) | `registration_number` | Operator | `"AB-CD 1234"` |
| Operator code (raw byte from the key) | `operator` | *(local only)* | `"3"` |
| iButton ROM ID | `key_id` | KeyID | `"0c-0000000000ab"` |
| Dedup content hash | `hash` | *(local only)* | `sha256(...)` |

## 🔬 Protocol Notes — How the Format Was Determined

The on-key format is undocumented by the vendor and was reverse-engineered by:
1. Disassembling the original PIUSI "Self.exe"/"SelfComm.exe" Windows applications to identify the low-level 1-Wire command sequence (`TMTouchReset`/`TMTouchByte`, Skip ROM, Read Memory `0xF0`).
2. Re-implementing that sequence from scratch in Python against the documented, GPL-referenced DS2490 USB protocol (`ds9490_direct.py`) — no vendor code is reused.
3. Cross-validating the decoded fields against an authoritative export from the vendor's own "SelfService" database, matching vehicle name, date, time, and liters exactly across multiple independent transactions.

**Key findings:**
- Memory is a **255-slot ring buffer** (32 bytes/slot) following a 16-byte header (2 unknown bytes + null-terminated firmware version string + 6-digit station number).
- Oldest entries are overwritten first once the buffer is full — physical address order is **not** chronological across the whole buffer (it resets in segments each time the buffer wraps).
- Within each 32-byte slot, the "name" half actually belongs to the **data half of the previous slot**, not its own — an off-by-one relationship that only became obvious once cross-checked against real transaction exports.
- Liters are encoded as **three consecutive BCD bytes concatenated into a 6-digit number, divided by 100** (e.g. `00 45 80` → `"004580"` → `45.80` L) — not a plain binary integer.

## 🛡️ Disclaimer

This application is an independent community development project and is not affiliated, authorized, maintained, or endorsed by **PIUSI SpA**. The on-key data format described above was determined through independent reverse engineering for interoperability purposes. Use at your own discretion — in particular, test `DELETE_KEY_AFTER_SYNC` thoroughly against a non-critical key before relying on it in production, as it permanently erases transaction data from the physical device.
