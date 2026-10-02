import hashlib
import os
import platform
import re
import time
from datetime import datetime

from dotenv import load_dotenv

# Import custom architecture modules
from database import FuelDatabase
from ds9490_direct import (
    DS9490,
    DS2490Error,
    erase_ds1996_memory,
    find_ds1996_rom_id_usb,
    read_ds1996_memory,
    scan_records,
)
from nextcloud import NextcloudTablesSync
from paths import APP_DIR as SCRIPT_DIR
from paths import ENV_PATH

if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)

# Config orchestration fallbacks
DB_PATH = os.getenv("DB_PATH") or os.path.join(SCRIPT_DIR, "FluidTrack.db")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(SCRIPT_DIR, DB_PATH)
DB_TABLE = os.getenv("DB_TABLE_NAME", "FuelTransactions")

# Safety-relevant feature flag: OFF by default. Only when explicitly set to
# "true"/"1"/"yes" is the iButton erased after a confirmed sync.
DELETE_KEY_AFTER_SYNC = os.getenv("DELETE_KEY_AFTER_SYNC", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

W1_DEVICES_DIR = "/sys/bus/w1/devices/"
DS1996_FAMILY_PREFIX = "0c-"

POLL_INTERVAL_SECONDS = 5
PENDING_SYNC_INTERVAL_SECONDS = 60


def set_delete_key_after_sync(enabled):
    """Switches the erase-after-sync behavior at runtime (used by the GUI).
    poll_once() reads the flag on every cycle, so this takes effect immediately."""
    global DELETE_KEY_AFTER_SYNC
    DELETE_KEY_AFTER_SYNC = bool(enabled)


def _find_ds1996_rom_id_sysfs():
    """
    Linux only: asks the w1 kernel subsystem which ROM IDs are on the bus.
    Returns (available, rom_id): available=False if no w1 bus master exists
    (Windows, module not loaded, or the kernel driver was detached by an
    earlier direct USB access).

    Explicitly filters for the DS1996 family (prefix '0c-'), since a second
    device without user memory can also be on the bus (e.g. a DS1420
    identification chip, family 0x81).
    """
    if not os.path.isdir(W1_DEVICES_DIR):
        return False, None

    master_dirs = [d for d in os.listdir(W1_DEVICES_DIR) if d.startswith("w1_bus_master")]
    if not master_dirs:
        return False, None

    slaves_file = os.path.join(W1_DEVICES_DIR, master_dirs[0], "w1_master_slaves")
    if not os.path.exists(slaves_file):
        return False, None

    with open(slaves_file, "r") as f:
        candidates = [line.strip() for line in f if line.strip() and "not found" not in line]

    for rom_id in candidates:
        if rom_id.lower().startswith(DS1996_FAMILY_PREFIX):
            return True, rom_id
    return True, None


_last_usb_error = None


def find_ds1996_rom_id():
    """
    Detects the DS1996 key's ROM ID. Uses the Linux w1 sysfs files when they
    exist (known to work), otherwise searches the 1-Wire bus directly over
    USB -- the only option on Windows. Both return the same ID notation.
    """
    global _last_usb_error
    available, rom_id = _find_ds1996_rom_id_sysfs()
    if available:
        return rom_id
    try:
        rom_id = find_ds1996_rom_id_usb()
        _last_usb_error = None
        return rom_id
    except DS2490Error as e:
        # Only log when the error changes, not on every poll cycle.
        if str(e) != _last_usb_error:
            print(f"[!] USB key detection failed: {e}")
            _last_usb_error = str(e)
        return None


def format_license_plate(raw_value):
    """
    Converts license plates from dot notation (DD.EE.12) into standard
    notation (DD-EE 12). Equipment names like 'FORKLIFT' or 'EXCAVATOR..NO',
    which don't match the plate pattern, are returned unchanged.
    """
    if not isinstance(raw_value, str):
        return raw_value

    # Regex pattern: 1-3 letters, a dot, 1-2 letters, a dot, 1-4 digits
    plate_pattern = r"^([A-Z]{1,3})\.([A-Z]{1,2})\.(\d{1,4})$"

    match = re.match(plate_pattern, raw_value.upper().strip())
    if match:
        city, letters, numbers = match.groups()
        return f"{city}-{letters} {numbers}"

    return raw_value


def build_record_hash(key_id, station, timestamp_iso, liters, operator, raw_name):
    """
    Creates a stable SHA-256 hash per transaction to detect duplicates --
    e.g. when the same key is read more than once before being
    synced/erased.

    Based ONLY on content fields that don't change -- NOT on the physical
    memory address, since the ring buffer on the key reuses addresses after
    a wrap, which would otherwise change the hash depending on when it's read.
    """
    payload = f"{key_id}|{station}|{timestamp_iso}|{liters:.2f}|{operator}|{raw_name}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_all_transactions(rom_id):
    """
    Reads the complete DS1996 memory over USB (DS2490 protocol) and returns
    a list of fully parsed transactions -- including a combined ISO
    timestamp (instead of separate date/time strings) and a dedup hash.
    """
    with DS9490() as ds:
        raw_data = read_ds1996_memory(ds, rom_id, start_addr=0, length=8192)

    # Station number from the 16-byte header (bytes 10-15, see reverse-engineering notes)
    station = raw_data[10:16].decode("ascii", errors="replace").strip()

    transactions = []
    for record in scan_records(raw_data):
        # Combine the separate 'date'/'time' strings into a single timestamp
        timestamp_dt = datetime.strptime(f"{record['date']} {record['time']}", "%Y-%m-%d %H:%M")
        timestamp_iso = timestamp_dt.isoformat()

        raw_name = record["name"]
        vehicle = format_license_plate(raw_name)

        record_hash = build_record_hash(
            rom_id, station, timestamp_iso, record["liters"], record["operator"], raw_name
        )

        transactions.append(
            {
                "hash": record_hash,
                "key_id": rom_id,
                "station": station,
                "timestamp": timestamp_iso,
                "liters": record["liters"],
                "operator": record["operator"],
                "vehicle": vehicle,
            }
        )

    return transactions


def initialize():
    """
    One-time setup: creates the DB/Nextcloud clients, initializes the local
    schema, and checks whether Nextcloud is configured. Returns
    (db, cloud, nextcloud_enabled) so both the CLI entry point and other
    front-ends (e.g. a tray app) can reuse the same setup logic.
    """
    print(
        f"[+] Initializing FluidTrack-Mini runtime environment on {platform.system()}..."
    )

    db = FuelDatabase(DB_PATH, DB_TABLE)
    cloud = NextcloudTablesSync()

    db.init_database()
    cloud.verify_or_create_table()

    nextcloud_enabled = cloud.is_configured()
    if nextcloud_enabled:
        print("[C] Nextcloud sync is configured and active.")
    else:
        print("[i] Nextcloud is not configured -- sync will be skipped, "
              "transactions are only stored locally in SQLite.")

    return db, cloud, nextcloud_enabled


_last_pending_sync = 0.0


def sync_pending_transactions(db, cloud):
    """Periodic Nextcloud maintenance, rate-limited to once per
    PENDING_SYNC_INTERVAL_SECONDS so an offline server doesn't flood the log:
      1. checks the connection and makes sure the table exists -- a missing
         or stale NEXTCLOUD_TABLE_ID is resolved/re-created automatically,
      2. retries the upload for records stored locally but not yet
         confirmed (sent=0), e.g. after a network outage."""
    global _last_pending_sync
    if time.time() - _last_pending_sync < PENDING_SYNC_INTERVAL_SECONDS:
        return
    _last_pending_sync = time.time()

    if not cloud.check_connection():
        cloud.reconciled = False  # do a full comparison again once reconnected
        return

    if not cloud.reconciled:
        reconcile_with_nextcloud(db, cloud)
        return

    pending = db.get_unsynced_transactions()
    if not pending:
        return

    print(f"[C] Retrying Nextcloud upload for {len(pending)} pending transaction(s)...")
    synced = 0
    for record_id, station, timestamp, liters, vehicle, key_id, record_hash in pending:
        if not cloud.upload_row(
            station=station,
            timestamp=timestamp,
            liters=liters,
            operator=vehicle,
            key_id=key_id,
            record_hash=record_hash,
        ):
            break  # server still unreachable/broken -- try again next interval
        db.mark_as_synced(record_id)
        synced += 1
    print(f"[C] {synced}/{len(pending)} pending transaction(s) synced.")


def reconcile_with_nextcloud(db, cloud):
    """Compares ALL local records with the Nextcloud table (by hash) and
    uploads whatever is missing there. Runs at startup and after every
    reconnect, so records are synced even if the local 'sent' flag is wrong
    (e.g. stored while Nextcloud was not configured or unreachable)."""
    try:
        uploaded = cloud.get_uploaded_hashes()
    except Exception as e:
        print(f"[!] Could not read the Nextcloud table for comparison: {e}")
        return

    records = db.get_sync_candidates()
    missing = [r for r in records if r[1] not in uploaded]
    present_ids = [r[0] for r in records if r[1] in uploaded and r[7] != 1]
    db.set_sent(present_ids, True)
    # Records the DB thinks are synced but that are not in Nextcloud.
    db.set_sent([r[0] for r in missing if r[7] == 1], False)

    print(
        f"[C] Nextcloud comparison: {len(records) - len(missing)} of {len(records)} "
        f"local transaction(s) already in Nextcloud, {len(missing)} to upload."
    )

    synced = 0
    for record_id, record_hash, station, timestamp, liters, vehicle, key_id, _ in missing:
        if not cloud.upload_row(
            station=station,
            timestamp=timestamp,
            liters=liters,
            operator=vehicle,
            key_id=key_id,
            record_hash=record_hash,
        ):
            print(f"[!] Upload stopped after {synced}/{len(missing)}, will retry later.")
            return
        db.mark_as_synced(record_id)
        synced += 1
        if synced % 50 == 0:
            print(f"[C] {synced}/{len(missing)} uploaded...")

    if missing:
        print(f"[C] {synced} transaction(s) uploaded to Nextcloud.")
    cloud.reconciled = True


def poll_once(db, cloud, nextcloud_enabled):
    """
    Runs exactly ONE detection/processing cycle: checks for a key, reads and
    parses its transactions if present, stores/syncs new ones, and performs
    the optional erase. Contains no sleep/looping -- callers (CLI loop, or a
    tray app's worker thread) are responsible for the polling interval.
    """
    if nextcloud_enabled:
        sync_pending_transactions(db, cloud)

    rom_id = find_ds1996_rom_id()

    if not rom_id:
        print("[-] Hardware polling: no DS1996 key (family 0c-) detected on the bus.")
        return

    print(f"[K] Key detected: {rom_id}")

    try:
        transactions = read_all_transactions(rom_id)
    except DS2490Error as e:
        print(f"[!] USB read error: {e}")
        transactions = []

    if not transactions:
        print("[+] No valid transactions found on the key.")
        return

    new_count = 0
    for tx in transactions:
        if db.record_exists(tx["hash"]):
            continue  # already known (DB or a previous read) -> skip

        print(
            f"[N] New transaction -> {tx['vehicle']} | "
            f"{tx['timestamp']} | {tx['liters']:.2f} L"
        )

        record_id = db.insert_transaction(
            record_hash=tx["hash"],
            key_id=tx["key_id"],
            station=tx["station"],
            timestamp=tx["timestamp"],
            liters=tx["liters"],
            operator=tx["operator"],
            vehicle=tx["vehicle"],
        )
        new_count += 1

        if record_id is None:
            continue

        if nextcloud_enabled:
            uploaded = cloud.upload_row(
                station=tx["station"],
                timestamp=tx["timestamp"],
                liters=tx["liters"],
                operator=tx["vehicle"],
                key_id=tx["key_id"],
                record_hash=tx["hash"],
            )
            if uploaded:
                db.mark_as_synced(record_id)
                print("    [C] Synced to Nextcloud.")
            else:
                # Stays sent=0 -> retried by sync_pending_transactions().
                print(f"    [!] Nextcloud sync failed ({tx['hash'][:8]}...), will retry later.")
        else:
            # Nextcloud is optional/not configured: local storage already
            # counts as done in this case (relevant for the erase
            # eligibility check below).
            db.mark_as_synced(record_id)
            print("    [i] Stored locally only (Nextcloud not configured).")

    if new_count == 0:
        print("[+] All transactions already known -- no new entries.")
    else:
        print(f"[+] {new_count} new transaction(s) processed.")

    # --- Optional key erasure (only if enabled via .env) ---
    if DELETE_KEY_AFTER_SYNC:
        all_confirmed_synced = all(db.is_synced(tx["hash"]) for tx in transactions)
        if not all_confirmed_synced:
            print(
                "[!] Erase skipped: not all records on the key are "
                "confirmed as synced (sent=1)."
            )
        else:
            print(
                "[E] DELETE_KEY_AFTER_SYNC is enabled and all records are "
                "confirmed synced -- erasing key memory..."
            )
            try:
                with DS9490() as ds:
                    erase_ds1996_memory(ds, rom_id)

                    # Safety verification: after erasing, scan_records() should
                    # no longer find anything.
                    verify_data = read_ds1996_memory(
                        ds, rom_id, start_addr=0, length=8192
                    )
                remaining = scan_records(verify_data)
                if remaining:
                    print(
                        f"[!] WARNING: {len(remaining)} record(s) were "
                        f"still found after erasing!"
                    )
                else:
                    print("[E] Key successfully erased and verified (blank).")
            except DS2490Error as e:
                print(f"[!] Error while erasing the key: {e}")


def main():
    """CLI entry point: initializes once, then polls forever every POLL_INTERVAL_SECONDS."""
    db, cloud, nextcloud_enabled = initialize()

    print("[R] Operational polling sequence started. Press CTRL+C to terminate.")

    try:
        while True:
            poll_once(db, cloud, nextcloud_enabled)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\n[X] Process termination intercepted. Shutting down FluidTrack-Mini.")


if __name__ == "__main__":
    main()
