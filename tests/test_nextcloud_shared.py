"""
Tests for several users/installations sharing ONE Nextcloud table (no network).

Run:  make test   (or: uv run python -m unittest discover -s tests -v)
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import nextcloud  # noqa: E402

OWN_PERMS = {"read": True, "create": False, "update": False, "delete": False, "manage": False}


def table(table_id, created, shared=False, owner="chris", title="FuelTransactions", **perms):
    permissions = dict(OWN_PERMS)
    permissions.update(perms)
    return {
        "id": table_id,
        "title": title,
        "createdAt": created,
        "isShared": shared,
        "ownership": owner,
        "ownerDisplayName": owner.capitalize(),
        "archived": False,
        "onSharePermissions": permissions,
    }


COLUMNS = [{"title": t, "id": i} for i, t in enumerate(nextcloud.COLUMN_DEFINITIONS, start=100)]


class FakeServer:
    """Answers the Tables API calls made by NextcloudTablesSync."""

    def __init__(self, tables, columns=COLUMNS, shares=()):
        self.tables = list(tables)
        self.columns = list(columns)
        self.shares = list(shares)
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if (method, path) == ("GET", "/tables"):
            return self.tables
        if method == "GET" and path.endswith("/columns"):
            return self.columns
        if method == "GET" and path.endswith("/shares"):
            return self.shares
        if method == "POST" and path.endswith("/shares"):
            self.shares.append(kwargs["json"])
            return {"id": len(self.shares)}
        if (method, path) == ("POST", "/tables"):
            new = table(99, "2026-10-05 10:00:00")
            self.tables.append(new)
            return new
        if method == "POST" and path.endswith("/columns"):
            raise RuntimeError("HTTP 403: not allowed")
        raise AssertionError(f"unexpected call {method} {path}")


def make_sync(server, table_id="", share_with=""):
    env = {
        "NEXTCLOUD_URL": "https://cloud.example.com",
        "NEXTCLOUD_USER": "anna",
        "NEXTCLOUD_APP_TOKEN": "pw",
        "NEXTCLOUD_TABLE_ID": table_id,
        "NEXTCLOUD_SHARE_WITH": share_with,
        "DB_TABLE_NAME": "FuelTransactions",
    }
    with mock.patch.dict(os.environ, env):
        sync = nextcloud.NextcloudTablesSync()
    sync._request = server
    sync._write_id_to_env = mock.Mock()
    return sync


def quiet():
    return mock.patch("builtins.print")


class TableChoiceTests(unittest.TestCase):
    def test_every_user_picks_the_same_oldest_table(self):
        """Anna sees her own (newer) table and Chris's shared (older) one."""
        tables = [
            table(12, "2026-10-04 08:00:00", owner="anna"),
            table(10, "2026-10-02 09:20:32", shared=True, owner="chris", create=True),
        ]
        with quiet():
            self.assertEqual(make_sync(FakeServer(tables))._find_table(), "10")

    def test_stored_own_table_id_does_not_block_the_shared_table(self):
        tables = [
            table(12, "2026-10-04 08:00:00", owner="anna"),
            table(10, "2026-10-02 09:20:32", shared=True, owner="chris", create=True),
        ]
        with quiet():
            self.assertEqual(make_sync(FakeServer(tables), table_id="12")._find_table(), "10")

    def test_read_only_share_is_not_used(self):
        tables = [table(10, "2026-10-02 09:20:32", shared=True, owner="chris", create=False)]
        with quiet() as printed:
            self.assertIsNone(make_sync(FakeServer(tables))._find_table())
        self.assertIn("read-only", " ".join(str(c.args) for c in printed.call_args_list))

    def test_own_table_counts_as_writable(self):
        """The API reports create=False for OWN tables -- must not be misread."""
        self.assertEqual(make_sync(FakeServer([table(10, "2026-10-02")]))._find_table(), "10")

    def test_other_titles_and_archived_tables_ignored(self):
        archived = table(3, "2020-01-01")
        archived["archived"] = True
        tables = [archived, table(4, "2021-01-01", title="Holidays"), table(10, "2026-10-02")]
        self.assertEqual(make_sync(FakeServer(tables))._find_table(), "10")


class SharingTests(unittest.TestCase):
    def test_parse_share_targets(self):
        self.assertEqual(
            nextcloud.parse_share_targets(" Fahrer, user:anna ,group:Büro,,"),
            [("group", "Fahrer"), ("user", "anna"), ("group", "Büro")],
        )

    def test_new_table_is_shared_with_create_rights_only(self):
        server = FakeServer([])
        sync = make_sync(server, share_with="Fahrer")
        with quiet():
            self.assertTrue(sync.verify_or_create_table())
        self.assertEqual(len(server.shares), 1)
        share = server.shares[0]
        self.assertEqual((share["receiverType"], share["receiver"]), ("group", "Fahrer"))
        self.assertTrue(share["permissionRead"] and share["permissionCreate"])
        self.assertFalse(
            share["permissionUpdate"] or share["permissionDelete"] or share["permissionManage"]
        )

    def test_existing_share_not_duplicated_and_checked_once(self):
        server = FakeServer(
            [table(10, "2026-10-02")], shares=[{"receiverType": "group", "receiver": "Fahrer"}]
        )
        sync = make_sync(server, share_with="Fahrer")
        with quiet():
            sync.verify_or_create_table()
            sync.verify_or_create_table()
        share_calls = [c for c in server.calls if c[1].endswith("/shares")]
        self.assertEqual(share_calls, [("GET", "/tables/10/shares", None)])

    def test_tables_of_others_are_not_reshared(self):
        server = FakeServer([table(10, "2026-10-02", shared=True, create=True)])
        sync = make_sync(server, share_with="Fahrer")
        with quiet():
            sync.verify_or_create_table()
        self.assertFalse([c for c in server.calls if c[1].endswith("/shares")])

    def test_shared_table_with_missing_columns_gives_clear_error(self):
        server = FakeServer(
            [table(10, "2026-10-02", shared=True, create=True)], columns=COLUMNS[:5]
        )
        sync = make_sync(server)
        with quiet() as printed:
            self.assertFalse(sync.verify_or_create_table())
        self.assertIn("Chris must start FluidTrack-Mini once", sync.last_error)
        self.assertTrue(printed.called)


class SwitchTests(unittest.TestCase):
    def test_switch_to_shared_table_triggers_full_comparison(self):
        server = FakeServer([table(12, "2026-10-04", owner="anna")])
        sync = make_sync(server)
        with quiet():
            sync.verify_or_create_table()
        sync.reconciled = True
        # Chris shares the older table with Anna.
        server.tables.append(table(10, "2026-10-02", shared=True, create=True))
        with quiet():
            sync.check_connection()
        self.assertEqual(sync.table_id, "10")
        self.assertFalse(sync.reconciled)


class SkipKnownUploadTests(unittest.TestCase):
    def test_new_transaction_already_uploaded_elsewhere_is_not_uploaded_again(self):
        import main

        tx = {
            "hash": "h1",
            "key_id": "0c-0000001db780",
            "station": "100001",
            "timestamp": "2026-10-05T08:15:00",
            "liters": 42.17,
            "operator": 3,
            "vehicle": "AB-CD 999",
        }
        db = mock.Mock()
        db.record_exists.return_value = False
        db.insert_transaction.return_value = 7
        cloud = mock.Mock()
        cloud.is_ready.return_value = True
        cloud.get_uploaded_hashes.return_value = {"h1"}
        main._key_on_reader = None
        with (
            mock.patch.object(main, "find_ds1996_rom_id", return_value=tx["key_id"]),
            mock.patch.object(main, "read_all_transactions", return_value=[tx]),
            mock.patch.object(main, "sync_pending_transactions"),
            mock.patch.object(main, "DELETE_KEY_AFTER_SYNC", False),
            quiet(),
        ):
            main.poll_once(db, cloud, True)
        cloud.upload_row.assert_not_called()
        db.mark_as_synced.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
