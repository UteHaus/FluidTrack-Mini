"""
Tests for the key state reported to the GUI by main.poll_once (no hardware).

Run:  make test   (or: uv run python -m unittest discover -s tests -v)
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main  # noqa: E402

ROM = "0c-0000001db780"
TX = {
    "hash": "h1",
    "key_id": ROM,
    "station": "100001",
    "timestamp": "2026-10-05T08:15:00",
    "liters": 42.17,
    "operator": 3,
    "vehicle": "AB-CD 999",
}


class FakeDB:
    def __init__(self):
        self.rows = {}  # hash -> sent

    def record_exists(self, record_hash):
        return record_hash in self.rows

    def insert_transaction(self, record_hash, **kwargs):
        self.rows[record_hash] = False
        return record_hash

    def mark_as_synced(self, record_id):
        self.rows[record_id] = True

    def is_synced(self, record_hash):
        return self.rows.get(record_hash, False)


class KeyStateTests(unittest.TestCase):
    def setUp(self):
        main._key_on_reader = None
        self.db = FakeDB()
        self.cloud = mock.Mock()
        self.cloud.is_ready.return_value = False  # no hash lookup
        self.cloud.upload_row.return_value = True
        self.states = []
        self.present = ROM
        self.read = mock.Mock(return_value=[TX])
        self.erase = mock.Mock(return_value=True)
        patches = [
            mock.patch.object(main, "find_ds1996_rom_id", lambda: self.present),
            mock.patch.object(main, "read_all_transactions", self.read),
            mock.patch.object(main, "sync_pending_transactions"),
            mock.patch.object(main, "_erase_key", self.erase),
            mock.patch.object(main, "DELETE_KEY_AFTER_SYNC", False),
            mock.patch("builtins.print"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def cycle(self):
        main.poll_once(self.db, self.cloud, True, on_key_state=lambda s, r: self.states.append(s))

    def test_reading_then_synced(self):
        self.cycle()
        self.assertEqual(self.states, [main.KEY_READING, main.KEY_SYNCED])
        self.assertTrue(self.db.is_synced("h1"))

    def test_key_left_on_reader_is_not_read_again(self):
        self.cycle()
        self.cycle()
        self.cycle()
        self.assertEqual(self.read.call_count, 1)
        self.assertEqual(self.states[2:], [main.KEY_SYNCED, main.KEY_SYNCED])

    def test_removed_and_placed_again_is_read_again(self):
        self.cycle()
        self.present = None
        self.cycle()
        self.present = ROM
        self.cycle()
        self.assertEqual(self.read.call_count, 2)
        self.assertEqual(
            self.states,
            [main.KEY_READING, main.KEY_SYNCED, main.KEY_IDLE, main.KEY_READING, main.KEY_SYNCED],
        )

    def test_no_idle_signal_without_a_key_before(self):
        self.present = None
        self.cycle()
        self.assertEqual(self.states, [])

    def test_read_error_is_retried(self):
        self.read.side_effect = [main.DS2490Error("contact lost"), [TX]]
        self.cycle()
        self.cycle()
        self.assertEqual(
            self.states, [main.KEY_READING, main.KEY_ERROR, main.KEY_READING, main.KEY_SYNCED]
        )

    def test_pending_until_upload_succeeds_without_rereading(self):
        self.cloud.upload_row.return_value = False  # Nextcloud down
        self.cycle()
        self.assertEqual(self.states[-1], main.KEY_PENDING)
        self.db.mark_as_synced("h1")  # the retry in sync_pending_transactions succeeded
        self.cycle()
        self.assertEqual(self.states[-1], main.KEY_SYNCED)
        self.assertEqual(self.read.call_count, 1)

    def test_erase_waits_for_sync_and_runs_once(self):
        self.cloud.upload_row.return_value = False
        with mock.patch.object(main, "DELETE_KEY_AFTER_SYNC", True):
            self.cycle()
            self.erase.assert_not_called()
            self.db.mark_as_synced("h1")
            self.cycle()
            self.cycle()
        self.erase.assert_called_once_with(ROM)
        # Erasing is shown as red (do not remove), then green.
        self.assertEqual(self.states[-3:], [main.KEY_READING, main.KEY_SYNCED, main.KEY_SYNCED])

    def test_erase_right_away_when_synced(self):
        with mock.patch.object(main, "DELETE_KEY_AFTER_SYNC", True):
            self.cycle()
            self.cycle()
        self.erase.assert_called_once_with(ROM)
        self.assertEqual(self.read.call_count, 1)

    def test_callback_errors_do_not_break_the_cycle(self):
        def broken(state, rom):
            raise RuntimeError("UI gone")

        main.poll_once(self.db, self.cloud, True, on_key_state=broken)
        self.assertTrue(self.db.is_synced("h1"))


if __name__ == "__main__":
    unittest.main()
