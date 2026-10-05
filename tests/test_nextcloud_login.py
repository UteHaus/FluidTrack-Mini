"""
Tests for the Nextcloud login helpers and the table lookup (no network).

Run:  make test   (or: uv run python -m unittest discover -s tests -v)
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import nextcloud  # noqa: E402
import nextcloud_login as nl  # noqa: E402


class NormalizeUrlTests(unittest.TestCase):
    def test_variants_of_user_input(self):
        cases = {
            "cloud.example.com": "https://cloud.example.com",
            "  https://cloud.example.com/  ": "https://cloud.example.com",
            "http://cloud.example.com": "http://cloud.example.com",
            "https://cloud.example.com/apps/files/?dir=/": "https://cloud.example.com",
            "https://cloud.example.com/index.php/apps/dashboard/": "https://cloud.example.com",
            "https://example.com/nextcloud/apps/files/": "https://example.com/nextcloud",
            "example.com:8443/nc": "https://example.com:8443/nc",
            "https://cloud.example.com/s/AbC123": "https://cloud.example.com",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(nl.normalize_nextcloud_url(raw), expected)

    def test_invalid_input(self):
        for raw in ("", "   ", "ftp://cloud.example.com", "https://"):
            with self.subTest(raw=raw), self.assertRaises(nl.NextcloudLoginError):
                nl.normalize_nextcloud_url(raw)


def fake_response(status, json_data=None, location=None):
    response = mock.Mock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.is_redirect = status in (301, 302, 303, 307, 308) and location is not None
    response.is_permanent_redirect = status in (301, 308) and location is not None
    response.headers = {"Location": location} if location else {}
    response.json.return_value = json_data
    if json_data is None:
        response.json.side_effect = ValueError("no json")
    return response


FLOW = {
    "login": "https://cloud.example.com/login/v2/flow/x",
    "poll": {"token": "t", "endpoint": "e"},
}


class StartLoginFlowTests(unittest.TestCase):
    def test_redirect_keeps_post(self):
        """http -> https redirect: requests would turn the POST into a GET (405)."""
        responses = [
            fake_response(301, location="https://cloud.example.com/index.php/login/v2"),
            fake_response(200, FLOW),
        ]
        with mock.patch.object(nl.requests, "post", side_effect=responses) as post:
            result = nl.start_login_flow("http://cloud.example.com")
        self.assertEqual(result, ("https://cloud.example.com/login/v2/flow/x", "t", "e"))
        self.assertEqual(
            [c.args[0] for c in post.call_args_list],
            [
                "http://cloud.example.com/index.php/login/v2",
                "https://cloud.example.com/index.php/login/v2",
            ],
        )
        self.assertTrue(all(c.kwargs["allow_redirects"] is False for c in post.call_args_list))

    def test_not_a_nextcloud(self):
        for response in (fake_response(404), fake_response(405), fake_response(200, {"x": 1})):
            with self.subTest(status=response.status_code):
                with mock.patch.object(nl.requests, "post", return_value=response):
                    with self.assertRaises(nl.NextcloudLoginError):
                        nl.start_login_flow("cloud.example.com")

    def test_unreachable_server(self):
        error = nl.requests.ConnectionError("Name or service not known")
        with mock.patch.object(nl.requests, "post", side_effect=error):
            with self.assertRaises(nl.NextcloudLoginError) as ctx:
                nl.start_login_flow("cloud.example.com")
        self.assertIn("Cannot reach", str(ctx.exception))

    def test_redirect_loop(self):
        loop = fake_response(302, location="/index.php/login/v2")
        with mock.patch.object(nl.requests, "post", return_value=loop):
            with self.assertRaises(nl.NextcloudLoginError):
                nl.start_login_flow("cloud.example.com")


class OpenBrowserTests(unittest.TestCase):
    def test_frozen_linux_restores_library_path(self):
        env = {
            "LD_LIBRARY_PATH": "/app/_internal",
            "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
            "HOME": "/home/x",
        }
        with (
            mock.patch.object(nl.sys, "frozen", True, create=True),
            mock.patch.object(nl.sys, "platform", "linux"),
            mock.patch.dict(nl.os.environ, env, clear=True),
            mock.patch.object(nl.shutil, "which", return_value="/usr/bin/xdg-open"),
            mock.patch.object(nl.subprocess, "Popen") as popen,
        ):
            self.assertTrue(nl.open_browser("https://example.com/login"))
        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(child_env["LD_LIBRARY_PATH"], "/usr/local/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", child_env)
        self.assertEqual(
            popen.call_args.args[0], ["/usr/bin/xdg-open", "https://example.com/login"]
        )

    def test_frozen_linux_without_original_path_drops_it(self):
        env = {"LD_LIBRARY_PATH": "/app/_internal"}
        with (
            mock.patch.object(nl.sys, "frozen", True, create=True),
            mock.patch.object(nl.sys, "platform", "linux"),
            mock.patch.dict(nl.os.environ, env, clear=True),
            mock.patch.object(nl.shutil, "which", return_value="/usr/bin/xdg-open"),
            mock.patch.object(nl.subprocess, "Popen") as popen,
        ):
            nl.open_browser("https://example.com")
        self.assertNotIn("LD_LIBRARY_PATH", popen.call_args.kwargs["env"])

    def test_reports_failure(self):
        with mock.patch.object(nl.webbrowser, "open", return_value=False):
            self.assertFalse(nl.open_browser("https://example.com"))


class CredentialsTests(unittest.TestCase):
    def test_login_clears_table_id(self):
        values = nl.credentials_to_env(
            {"server": "https://new.example.com", "loginName": "anna", "appPassword": "pw"}
        )
        self.assertEqual(values["NEXTCLOUD_TABLE_ID"], "")
        self.assertEqual(values["NEXTCLOUD_URL"], "https://new.example.com")


class FindTableTests(unittest.TestCase):
    def _sync(self, table_id, tables):
        env = {
            "NEXTCLOUD_URL": "https://new.example.com",
            "NEXTCLOUD_USER": "anna",
            "NEXTCLOUD_APP_TOKEN": "pw",
            "NEXTCLOUD_TABLE_ID": table_id,
            "DB_TABLE_NAME": "FuelTransactions",
        }
        with mock.patch.dict(os.environ, env):
            sync = nextcloud.NextcloudTablesSync()
        sync._request = mock.Mock(return_value=tables)
        return sync

    def test_id_of_foreign_table_is_not_used(self):
        """After switching servers, ID 10 may belong to someone else's table."""
        tables = [{"id": 10, "title": "Holiday planning"}, {"id": 3, "title": "FuelTransactions"}]
        with mock.patch("builtins.print"):
            self.assertEqual(self._sync("10", tables)._find_table(), "3")

    def test_stored_id_does_not_override_the_oldest_table(self):
        """All installations must pick the same table, so a stored ID only
        caches the choice -- the oldest table with the title wins."""
        tables = [{"id": 10, "title": "FuelTransactions"}, {"id": 3, "title": "FuelTransactions"}]
        with mock.patch("builtins.print"):
            self.assertEqual(self._sync("10", tables)._find_table(), "3")

    def test_missing_table_returns_none(self):
        tables = [{"id": 10, "title": "Holiday planning"}]
        with mock.patch("builtins.print"):
            self.assertIsNone(self._sync("10", tables)._find_table())


if __name__ == "__main__":
    unittest.main()
