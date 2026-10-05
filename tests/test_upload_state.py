"""
Tests for the upload activity reported to the GUI and the single-instance
lock (no hardware, no network).

Run:  make test   (or: uv run python -m unittest discover -s tests -v)
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main  # noqa: E402


class UploadEventsTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        main.set_upload_listener(self.events.append)
        self.addCleanup(main.set_upload_listener, None)
        patcher = mock.patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reconcile_reports_upload_start_and_end(self):
        db = mock.Mock()
        db.get_sync_candidates.return_value = [
            (1, "h1", "100001", "2026-10-05T04:01:00", 1.0, "X", "k", 0)
        ]
        cloud = mock.Mock()
        cloud.get_uploaded_hashes.return_value = set()
        cloud.upload_row.return_value = True
        main.reconcile_with_nextcloud(db, cloud)
        self.assertEqual(self.events, [True, False])

    def test_reconcile_without_missing_rows_reports_nothing(self):
        db = mock.Mock()
        db.get_sync_candidates.return_value = [(1, "h1", "s", "t", 1.0, "X", "k", 1)]
        cloud = mock.Mock()
        cloud.get_uploaded_hashes.return_value = {"h1"}
        main.reconcile_with_nextcloud(db, cloud)
        self.assertEqual(self.events, [])

    def test_end_is_reported_even_if_upload_breaks_off(self):
        db = mock.Mock()
        db.get_sync_candidates.return_value = [(1, "h1", "s", "t", 1.0, "X", "k", 0)]
        cloud = mock.Mock()
        cloud.get_uploaded_hashes.return_value = set()
        cloud.upload_row.return_value = False  # server gone
        main.reconcile_with_nextcloud(db, cloud)
        self.assertEqual(self.events, [True, False])

    def test_end_is_reported_after_an_exception(self):
        db = mock.Mock()
        db.get_unsynced_transactions.return_value = [(1, "s", "t", 1.0, "X", "k", "h1")]
        cloud = mock.Mock()
        cloud.check_connection.return_value = True
        cloud.reconciled = True
        cloud.is_ready.return_value = False
        cloud.upload_row.side_effect = RuntimeError("boom")
        main._last_pending_sync = 0
        with self.assertRaises(RuntimeError):
            main.sync_pending_transactions(db, cloud)
        self.assertEqual(self.events, [True, False])

    def test_listener_errors_do_not_break_uploads(self):
        main.set_upload_listener(mock.Mock(side_effect=RuntimeError("UI gone")))
        with main._uploading():
            pass  # must not raise


class InstanceLockTests(unittest.TestCase):
    def test_second_instance_on_same_database_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "FluidTrack.db")
            first = main.acquire_instance_lock(db_path)
            try:
                with self.assertRaises(main.AlreadyRunningError):
                    main.acquire_instance_lock(db_path)
            finally:
                first.close()
            # Released (e.g. app closed or crashed) -> can start again.
            main.acquire_instance_lock(db_path).close()

    def test_different_databases_do_not_block_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = main.acquire_instance_lock(os.path.join(tmp, "a.db"))
            b = main.acquire_instance_lock(os.path.join(tmp, "b.db"))
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
