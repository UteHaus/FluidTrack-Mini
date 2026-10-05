import contextlib
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
    erase_ds1996_records,
    find_ds1996_rom_id_usb,
    is_key_erased,
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

    with open(slaves_file) as f:
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


# --- Upload activity, reported to the GUI (Quit is disabled meanwhile) --------
_upload_listener = None


def set_upload_listener(callback):
    """callback(busy) is called with True when uploads to Nextcloud start and
    with False when they are finished (also after errors)."""
    global _upload_listener
    _upload_listener = callback


def _emit_upload(busy):
    if _upload_listener is not None:
        try:
            _upload_listener(busy)
        except Exception as e:
            print(f"[!] Upload listener failed: {e}")


@contextlib.contextmanager
def _uploading(active=True):
    if not active:
        yield
        return
    _emit_upload(True)
    try:
        yield
    finally:
        _emit_upload(False)


# --- Single instance per database --------------------------------------------
class AlreadyRunningError(RuntimeError):
    pass


_instance_lock = None


def acquire_instance_lock(db_path):
    """Allows only ONE running FluidTrack-Mini per database. Two instances
    would compare and upload the same records at the same time and create
    duplicate rows in Nextcloud. The OS releases the lock automatically when
    the process ends, even after a crash."""
    lock_file = open(db_path + ".lock", "a+")  # noqa: SIM115 -- kept open on purpose
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        lock_file.close()
        raise AlreadyRunningError(
            f"FluidTrack-Mini is already running with the database {db_path}. "
            "Close the other instance first."
        ) from e
    return lock_file


def initialize():
    """
    One-time setup: creates the DB/Nextcloud clients, initializes the local
    schema, and checks whether Nextcloud is configured. Returns
    (db, cloud, nextcloud_enabled) so both the CLI entry point and other
    front-ends (e.g. a tray app) can reuse the same setup logic.
    """
    global _instance_lock
    print(f"[+] Initializing FluidTrack-Mini runtime environment on {platform.system()}...")

    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    if _instance_lock is None:
        _instance_lock = acquire_instance_lock(DB_PATH)

    db = FuelDatabase(DB_PATH, DB_TABLE)
    cloud = NextcloudTablesSync()

    db.init_database()
    cloud.verify_or_create_table()

    nextcloud_enabled = cloud.is_configured()
    if nextcloud_enabled:
        print("[C] Nextcloud sync is configured and active.")
    else:
        print(
            "[i] Nextcloud is not configured -- sync will be skipped, "
            "transactions are only stored locally in SQLite."
        )

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
    in_cloud = _hashes_in_nextcloud(cloud)
    with _uploading():
        synced = 0
        for record_id, station, timestamp, liters, vehicle, key_id, record_hash in pending:
            if record_hash in in_cloud:
                db.mark_as_synced(record_id)  # uploaded by another installation
                synced += 1
                continue
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


def _hashes_in_nextcloud(cloud):
    """Hashes already in the Nextcloud table -- other installations may share
    the table and may have uploaded the same transactions already. Returns an
    empty set if the table can't be read (upload then proceeds as usual)."""
    if not cloud.is_ready():
        return set()
    try:
        return cloud.get_uploaded_hashes()
    except Exception as e:
        print(f"[!] Could not read existing rows from Nextcloud: {e}")
        return set()


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

    with _uploading(active=bool(missing)):
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


# --- Key state, reported to the GUI via poll_once(on_key_state=...) ----------
KEY_IDLE = "idle"  # no key on the reader
KEY_READING = "reading"  # key is being read/erased -- do not remove it
KEY_SYNCED = "synced"  # key processed, every record on it is synced
KEY_PENDING = "pending"  # key processed, stored locally, Nextcloud upload pending
KEY_ERROR = "error"  # reading failed, retried in the next cycle

# The key that is still on the reader and was already processed completely:
# {"rom_id", "hashes", "erase_attempted"}. A key is read once per placement --
# re-reading it every cycle (~4 s each) would only repeat the same work.
_key_on_reader = None


def _notify(on_key_state, state, rom_id):
    if on_key_state is not None:
        try:
            on_key_state(state, rom_id)
        except Exception as e:
            print(f"[!] Key state callback failed: {e}")


def _store_and_upload(db, cloud, nextcloud_enabled, transactions):
    """Stores new transactions locally and uploads them to Nextcloud."""
    new_count = 0
    in_cloud = None  # fetched lazily, once per cycle, only if there is something new
    for tx in transactions:
        if db.record_exists(tx["hash"]):
            continue  # already known (DB or a previous read) -> skip

        print(f"[N] New transaction -> {tx['vehicle']} | {tx['timestamp']} | {tx['liters']:.2f} L")

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
            if in_cloud is None:
                in_cloud = _hashes_in_nextcloud(cloud)
            if tx["hash"] in in_cloud:
                db.mark_as_synced(record_id)
                print("    [C] Already in Nextcloud (uploaded by another installation).")
                continue
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


def _erase_key(rom_id):
    """Erases the key (same procedure as the PIUSI software) and verifies it.
    Returns True only if the key reads back as fully erased."""
    print(
        "[E] DELETE_KEY_AFTER_SYNC is enabled and all records are "
        "confirmed synced -- erasing key memory..."
    )
    try:
        with DS9490() as ds:
            # Same procedure as the PIUSI software: blank all record
            # names and reset the write index (header byte 8).
            writes = erase_ds1996_records(ds, rom_id)

            # Safety verification: re-read the whole key.
            verify_data = read_ds1996_memory(ds, rom_id, start_addr=0, length=8192)
        remaining = scan_records(verify_data)
        if remaining or not is_key_erased(verify_data):
            print(
                f"[!] WARNING: key is not fully erased ({len(remaining)} record(s) still readable)!"
            )
        else:
            print(f"[E] Key erased and verified ({writes} page write(s)).")
            return True
    except DS2490Error as e:
        print(f"[!] Error while erasing the key: {e}")
    return False


def poll_once(db, cloud, nextcloud_enabled, on_key_state=None):
    """
    Runs exactly ONE detection/processing cycle: checks for a key, reads and
    parses its transactions if present, stores/syncs new ones, and performs
    the optional erase. Contains no sleep/looping -- callers (CLI loop, or a
    tray app's worker thread) are responsible for the polling interval.

    on_key_state(state, rom_id), if given, receives the KEY_* states, e.g.
    to show in a GUI that the key must not be removed right now.
    """
    global _key_on_reader
    if nextcloud_enabled:
        sync_pending_transactions(db, cloud)

    rom_id = find_ds1996_rom_id()

    if not rom_id:
        if _key_on_reader is not None:
            print("[K] Key removed.")
            _key_on_reader = None
            _notify(on_key_state, KEY_IDLE, None)
        print("[-] Hardware polling: no DS1996 key (family 0c-) detected on the bus.")
        return

    known = _key_on_reader if _key_on_reader and _key_on_reader["rom_id"] == rom_id else None
    if known is not None:
        # Same key still on the reader: no re-read. Only finish what had to
        # wait for the Nextcloud sync (the erase).
        all_synced = all(db.is_synced(h) for h in known["hashes"])
        if (
            all_synced
            and DELETE_KEY_AFTER_SYNC
            and known["hashes"]
            and not known["erase_attempted"]
        ):
            known["erase_attempted"] = True
            _notify(on_key_state, KEY_READING, rom_id)
            _erase_key(rom_id)
        _notify(on_key_state, KEY_SYNCED if all_synced else KEY_PENDING, rom_id)
        return

    print(f"[K] Key detected: {rom_id}")
    _notify(on_key_state, KEY_READING, rom_id)

    try:
        transactions = read_all_transactions(rom_id)
    except DS2490Error as e:
        print(f"[!] USB read error: {e}")
        _notify(on_key_state, KEY_ERROR, rom_id)
        return  # not remembered -> read again in the next cycle

    if transactions:
        with _uploading(active=nextcloud_enabled):
            _store_and_upload(db, cloud, nextcloud_enabled, transactions)
    else:
        print("[+] No valid transactions found on the key.")

    hashes = [tx["hash"] for tx in transactions]
    all_synced = all(db.is_synced(h) for h in hashes)
    erase_attempted = False
    # --- Optional key erasure (only if enabled) ---
    if DELETE_KEY_AFTER_SYNC and transactions:
        if all_synced:
            erase_attempted = True
            _erase_key(rom_id)
        else:
            print("[!] Erase postponed: not all records on the key are synced yet.")

    _key_on_reader = {"rom_id": rom_id, "hashes": hashes, "erase_attempted": erase_attempted}
    _notify(on_key_state, KEY_SYNCED if all_synced else KEY_PENDING, rom_id)


def run_cloud_sync():
    """One-off: compare the local DB with Nextcloud and upload what is missing
    (used by `make cloud-sync`). Returns a process exit code."""
    try:
        db, cloud, nextcloud_enabled = initialize()
    except AlreadyRunningError as e:
        print(f"[!] {e}")
        return 1
    if not nextcloud_enabled:
        print("[i] Nextcloud is not configured.")
        return 0
    sync_pending_transactions(db, cloud)
    return 0


def main():
    """CLI entry point: initializes once, then polls forever every POLL_INTERVAL_SECONDS."""
    try:
        db, cloud, nextcloud_enabled = initialize()
    except AlreadyRunningError as e:
        print(f"[!] {e}")
        raise SystemExit(1) from None

    print("[R] Operational polling sequence started. Press CTRL+C to terminate.")

    try:
        while True:
            poll_once(db, cloud, nextcloud_enabled)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\n[X] Process termination intercepted. Shutting down FluidTrack-Mini.")


if __name__ == "__main__":
    main()
