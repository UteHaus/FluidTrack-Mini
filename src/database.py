import os
import sqlite3


class FuelDatabase:
    def __init__(self, db_path, table_name):
        self.db_path = db_path
        self.table_name = table_name

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def init_database(self):
        """Creates the local data directory and schema if missing, then inserts sample data.
        Also migrates existing tables (from earlier versions) by adding any missing columns
        needed for hash-based deduplication and the iButton key mapping."""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
                print(f"[gear] Created missing database directory: {db_dir}")

            conn = self._connect()
            cursor = conn.cursor()

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
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
            conn.commit()

            # --- Schema migration: add columns needed for the iButton USB pipeline ---
            # (idempotent -- safe to run on every startup, e.g. against an older DB)
            cursor.execute(f"PRAGMA table_info({self.table_name})")
            existing_columns = {row[1] for row in cursor.fetchall()}

            if "key_id" not in existing_columns:
                cursor.execute(f"ALTER TABLE {self.table_name} ADD COLUMN key_id TEXT")
                print("[gear] Migrated schema: added 'key_id' column.")

            if "hash" not in existing_columns:
                cursor.execute(f"ALTER TABLE {self.table_name} ADD COLUMN hash TEXT")
                print("[gear] Migrated schema: added 'hash' column.")

            # Unique index enforces the deduplication at the DB level too (belt-and-braces
            # in addition to the record_exists() check done before every insert).
            cursor.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{self.table_name}_hash "
                f"ON {self.table_name}(hash)"
            )
            conn.commit()

            cursor.execute(f"SELECT COUNT(*) FROM {self.table_name}")
            if cursor.fetchone()[0] == 0:
                print(f"[gear] Populating '{self.table_name}' with initial sample data...")
                # Legacy sample rows (pre-iButton-USB-pipeline). Each gets a distinct
                # placeholder hash so the UNIQUE index doesn't reject them, and
                # key_id="LEGACY" marks them as not coming from a real key read.
                sample_rows = [
                    (
                        1,
                        "2020-01-10T07:35:00",
                        "100001",
                        1.94,
                        "1",
                        "",
                        0,
                        0,
                        0,
                        0,
                        "LEGACY",
                        "legacy-0001",
                    ),
                    (
                        1,
                        "2020-01-10T07:35:00",
                        "100001",
                        2.04,
                        "1",
                        "",
                        0,
                        0,
                        0,
                        0,
                        "LEGACY",
                        "legacy-0002",
                    ),
                    (
                        1,
                        "2020-01-10T09:42:00",
                        "100001",
                        3.06,
                        "2",
                        "",
                        0,
                        0,
                        0,
                        0,
                        "LEGACY",
                        "legacy-0003",
                    ),
                    (
                        1,
                        "2020-01-10T09:42:00",
                        "100001",
                        3.33,
                        "2",
                        "",
                        0,
                        0,
                        0,
                        0,
                        "LEGACY",
                        "legacy-0004",
                    ),
                    (
                        1,
                        "2020-01-12T08:06:00",
                        "100001",
                        3.01,
                        "2",
                        "",
                        0,
                        0,
                        0,
                        0,
                        "LEGACY",
                        "legacy-0005",
                    ),
                    (
                        1,
                        "2020-02-01T09:56:00",
                        "100001",
                        190.11,
                        "AB.CD.1234",
                        "",
                        0,
                        0,
                        0,
                        0,
                        "LEGACY",
                        "legacy-0006",
                    ),
                ]
                cursor.executemany(
                    f"""INSERT INTO {self.table_name}
                    (station_number, timestamp, serial_number, liters, operator,
                     registration_number, odometer, printed, sent, refused, key_id, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    sample_rows,
                )
                conn.commit()
                print("[ok] Sample data successfully imported.")

        except Exception as e:
            print(f"[-] Database initialization error: {e}")
        finally:
            conn.close()

    def record_exists(self, record_hash):
        """Checks whether a transaction with this dedup hash has already been stored --
        e.g. because the same iButton was read more than once before being synced/erased."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT 1 FROM {self.table_name} WHERE hash = ? LIMIT 1", (record_hash,)
            )
            return cursor.fetchone() is not None
        except Exception as e:
            print(f"[-] Error checking for existing record: {e}")
            return False
        finally:
            conn.close()

    def insert_transaction(
        self, record_hash, key_id, station, timestamp, liters, operator, vehicle
    ):
        """Inserts a freshly parsed iButton transaction.
        Returns the new row's id, or None on failure.

        Mapping onto the existing columns:
          serial_number        <- station (PIUSI station serial number, e.g. '100001')
          operator             <- operator (numeric operator code from the key, as text)
          registration_number  <- vehicle (formatted plate/equipment name, e.g. 'AB-CD 1234')
          key_id / hash        <- new columns for the physical key's ROM ID and the dedup hash
        """
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                f"""INSERT INTO {self.table_name}
                (station_number, timestamp, serial_number, liters, operator, registration_number,
                 odometer, printed, sent, refused, key_id, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    1,  # station_number: always 1 for now (single PIUSI station), extendable later
                    timestamp,
                    station,
                    liters,
                    str(operator),
                    vehicle,
                    0,  # odometer: not in the iButton format (see reverse-engineering notes)
                    0,  # printed
                    0,  # sent (set via mark_as_synced once the Nextcloud upload succeeds)
                    0,  # refused
                    key_id,
                    record_hash,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Should already be caught by record_exists() beforehand -- safety net
            # in case e.g. two processes try to insert the same record at once.
            print(f"[-] Duplicate hash, insert skipped: {record_hash[:8]}...")
            return None
        except Exception as e:
            print(f"[-] Error inserting transaction: {e}")
            return None
        finally:
            conn.close()

    def is_synced(self, record_hash):
        """Checks whether a record has already been successfully synced to Nextcloud
        (sent=1) -- used as a safety check before erasing the iButton."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT sent FROM {self.table_name} WHERE hash = ? LIMIT 1", (record_hash,)
            )
            row = cursor.fetchone()
            return row is not None and row[0] == 1
        except Exception as e:
            print(f"[-] Error checking sync status: {e}")
            return False
        finally:
            conn.close()

    def mark_as_synced(self, record_id):
        """Sets the 'sent' flag once a transaction has been successfully uploaded to Nextcloud."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE {self.table_name} SET sent = 1 WHERE id = ?", (record_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"[-] Error updating sync status: {e}")
            return False
        finally:
            conn.close()

    def get_sync_candidates(self):
        """Returns all real (non-sample) records with their sync state, for
        comparing the local DB against the Nextcloud table."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT id, hash, serial_number, timestamp, liters, registration_number, "
                f"key_id, sent FROM {self.table_name} "
                f"WHERE COALESCE(key_id, '') != 'LEGACY' ORDER BY timestamp"
            )
            return cursor.fetchall()
        except Exception as e:
            print(f"[-] Error fetching rows for sync: {e}")
            return []
        finally:
            conn.close()

    def set_sent(self, record_ids, sent):
        """Sets the 'sent' flag for several records at once."""
        if not record_ids:
            return
        try:
            conn = self._connect()
            conn.executemany(
                f"UPDATE {self.table_name} SET sent = ? WHERE id = ?",
                [(1 if sent else 0, rid) for rid in record_ids],
            )
            conn.commit()
        except Exception as e:
            print(f"[-] Error updating sync status: {e}")
        finally:
            conn.close()

    def get_unsynced_transactions(self):
        """Returns real (non-sample) records that are not yet confirmed as
        uploaded (sent=0), so failed Nextcloud uploads can be retried."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT id, serial_number, timestamp, liters, registration_number, key_id, hash "
                f"FROM {self.table_name} "
                f"WHERE sent = 0 AND COALESCE(key_id, '') != 'LEGACY' ORDER BY timestamp"
            )
            return cursor.fetchall()
        except Exception as e:
            print(f"[-] Error fetching unsynced rows: {e}")
            return []
        finally:
            conn.close()

    def get_unprinted_transactions(self):
        """Kept for backward compatibility with earlier tooling/reports."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            query = (
                f"SELECT id, station_number, timestamp, liters, operator "
                f"FROM {self.table_name} WHERE printed = 0 ORDER BY timestamp"
            )
            cursor.execute(query)
            return cursor.fetchall()
        except Exception as e:
            print(f"[-] Error fetching rows: {e}")
            return []
        finally:
            conn.close()

    def mark_as_printed(self, record_id):
        """Kept for backward compatibility with earlier tooling/reports."""
        try:
            conn = self._connect()
            cursor = conn.cursor()
            query = f"UPDATE {self.table_name} SET printed = 1 WHERE id = ?"
            cursor.execute(query, (record_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"[-] Error updating row status: {e}")
            return False
        finally:
            conn.close()
