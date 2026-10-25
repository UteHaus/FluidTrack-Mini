import sqlite3
from contextlib import closing


class FuelDatabase:
    def __init__(self, db_path, table_name):
        self.db_path = db_path
        self.table_name = table_name

    def _run(self, sql, params=(), *, many=False, error):
        """Runs one statement in its own committed transaction and returns all
        result rows. On failure, prints `error` and returns None."""
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                cur = conn.executemany(sql, params) if many else conn.execute(sql, params)
                return cur.fetchall()
        except Exception as e:
            print(f"[-] {error}: {e}")
            return None

    def init_database(self):
        """Creates the schema if missing. Also migrates existing tables (from
        earlier versions) by adding the columns needed for hash-based
        deduplication and the iButton key mapping."""
        t = self.table_name
        try:
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {t} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        station_number INTEGER,
                        timestamp TEXT NOT NULL,
                        serial_number TEXT,
                        liters REAL,
                        operator TEXT,
                        registration_number TEXT,
                        odometer INTEGER,
                        printed INTEGER DEFAULT 0,
                        sent INTEGER DEFAULT 0,
                        refused INTEGER DEFAULT 0
                    )
                """)
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({t})")}
                for column in ("key_id", "hash"):
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {t} ADD COLUMN {column} TEXT")
                        print(f"[gear] Migrated schema: added '{column}' column.")
                # Enforces the deduplication at the DB level too (in addition to
                # the record_exists() check done before every insert).
                conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{t}_hash ON {t}(hash)")
        except Exception as e:
            print(f"[-] Database initialization error: {e}")

    def record_exists(self, record_hash):
        """Checks whether a transaction with this dedup hash has already been stored --
        e.g. because the same iButton was read more than once before being synced/erased."""
        return bool(
            self._run(
                f"SELECT 1 FROM {self.table_name} WHERE hash = ? LIMIT 1",
                (record_hash,),
                error="Error checking for existing record",
            )
        )

    def insert_transaction(
        self, record_hash, key_id, station, timestamp, liters, operator, vehicle
    ):
        """Inserts a freshly parsed iButton transaction.
        Returns the new row's id, or None on failure (e.g. duplicate hash).

        Mapping onto the existing columns:
          serial_number        <- station (PIUSI station serial number, e.g. '100001')
          operator             <- operator (numeric operator code from the key, as text)
          registration_number  <- vehicle (formatted plate/equipment name, e.g. 'AB-CD 1234')
          key_id / hash        <- the physical key's ROM ID and the dedup hash
        station_number is always 1 (single station), odometer is not in the
        iButton format, printed/sent/refused start at 0.
        """
        rows = self._run(
            f"""INSERT INTO {self.table_name}
            (station_number, timestamp, serial_number, liters, operator, registration_number,
             odometer, printed, sent, refused, key_id, hash)
            VALUES (1, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?) RETURNING id""",
            (timestamp, station, liters, str(operator), vehicle, key_id, record_hash),
            error="Error inserting transaction",
        )
        return rows[0][0] if rows else None

    def is_synced(self, record_hash):
        """Checks whether a record has already been successfully synced to Nextcloud
        (sent=1) -- used as a safety check before erasing the iButton."""
        rows = self._run(
            f"SELECT sent FROM {self.table_name} WHERE hash = ? LIMIT 1",
            (record_hash,),
            error="Error checking sync status",
        )
        return bool(rows) and rows[0][0] == 1

    def set_sent(self, record_ids, sent=True):
        """Sets the 'sent' flag for one or more records."""
        if record_ids:
            self._run(
                f"UPDATE {self.table_name} SET sent = ? WHERE id = ?",
                [(1 if sent else 0, rid) for rid in record_ids],
                many=True,
                error="Error updating sync status",
            )

    def get_sync_candidates(self):
        """Returns all real (non-sample) records with their sync state, for
        comparing the local DB against the Nextcloud table."""
        return (
            self._run(
                f"SELECT id, hash, serial_number, timestamp, liters, registration_number, "
                f"key_id, sent FROM {self.table_name} "
                f"WHERE COALESCE(key_id, '') != 'LEGACY' ORDER BY timestamp",
                error="Error fetching rows for sync",
            )
            or []
        )

    def has_unsynced_transactions(self):
        """True if a real (non-sample) record is not yet confirmed as uploaded (sent=0)."""
        return bool(
            self._run(
                f"SELECT 1 FROM {self.table_name} "
                f"WHERE sent = 0 AND COALESCE(key_id, '') != 'LEGACY' LIMIT 1",
                error="Error fetching unsynced rows",
            )
        )
