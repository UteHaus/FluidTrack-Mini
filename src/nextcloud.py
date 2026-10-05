import os
from datetime import datetime

import requests
from requests.auth import HTTPBasicAuth

from i18n import t

# Nextcloud Tables REST API v1. Note: this lives under /index.php, NOT under
# /ocs/v2.php -- the OCS endpoint only exists as ".../api/2" and uses a
# different request format.
TABLES_API_PATH = "/index.php/apps/tables/api/1"

# Target schema: column title -> column definition for the Tables API.
# Rows are uploaded by column ID, so these titles are resolved to IDs at startup.
COLUMN_DEFINITIONS = {
    "Station": {"type": "text", "subtype": "line"},
    "Timestamp": {"type": "datetime", "subtype": ""},
    "Liters": {"type": "number", "numberDecimals": 2},
    "Operator": {"type": "text", "subtype": "line"},
    "KeyID": {"type": "text", "subtype": "line"},
    # Local dedup hash -- lets the app see which records already exist in
    # Nextcloud, independent of the local 'sent' flag.
    "Hash": {"type": "text", "subtype": "line"},
}
ROWS_PAGE_SIZE = 500


def parse_share_targets(value):
    """NEXTCLOUD_SHARE_WITH -> [(receiver_type, name)].
    Comma-separated; "user:anna" shares with a user, "group:Fahrer" or a
    plain name with a group."""
    targets = []
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        kind, _, name = item.partition(":")
        if name and kind.strip().lower() in ("user", "group"):
            targets.append((kind.strip().lower(), name.strip()))
        else:
            targets.append(("group", item))
    return targets


def _can_write(table):
    """Own tables are always writable. For tables shared WITH us, the share
    must grant 'create' (the API reports read-only-looking permissions for
    own tables, so they must not be judged by onSharePermissions)."""
    if not table.get("isShared"):
        return True
    return bool((table.get("onSharePermissions") or {}).get("create"))


class NextcloudTablesSync:
    def __init__(self):
        self.url = os.getenv("NEXTCLOUD_URL")
        self.user = os.getenv("NEXTCLOUD_USER")
        self.token = os.getenv("NEXTCLOUD_APP_TOKEN")
        self.table_title = os.getenv("DB_TABLE_NAME", "FuelTransactions")
        self.table_id = (os.getenv("NEXTCLOUD_TABLE_ID") or "").strip() or None
        self.column_ids = {}  # column title -> Nextcloud column ID

        # Connection state, read by the GUI. connected: None = not checked yet.
        self.connected = None
        self.last_error = None
        self.last_sync_at = None
        # Set once the local DB has been fully compared with the Nextcloud table.
        self.reconciled = False

        # Shared use: several users/installations write into ONE table.
        self.share_targets = parse_share_targets(os.getenv("NEXTCLOUD_SHARE_WITH", ""))
        self.table_info = {}  # API data of the selected table (owner, sharing)
        self._shares_checked = False
        self._reported_duplicates = None

        self.base_api_url = f"{self.url.rstrip('/')}{TABLES_API_PATH}" if self.url else ""
        self.headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
        self.auth = HTTPBasicAuth(self.user, self.token) if self.user and self.token else None

    def is_configured(self):
        """Returns whether Nextcloud is configured at all (URL + credentials
        present). Nextcloud is optional -- if not configured, the sync step
        is simply skipped instead of raising an error."""
        return bool(self.auth and self.base_api_url)

    def is_ready(self):
        """True once the target table and all its columns are resolved, i.e.
        rows can actually be uploaded."""
        return bool(self.table_id) and set(COLUMN_DEFINITIONS) <= set(self.column_ids)

    # ---------------------------------------------------------------
    # HTTP helpers
    # ---------------------------------------------------------------

    def _request(self, method, path, **kwargs):
        try:
            response = requests.request(
                method,
                f"{self.base_api_url}{path}",
                headers=self.headers,
                auth=self.auth,
                timeout=kwargs.pop("timeout", 15),
                **kwargs,
            )
        except requests.RequestException as e:
            self._set_state(False, f"Server not reachable: {e}")
            raise
        if not 200 <= response.status_code < 300:
            message = f"{method} {path} -> HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code in (401, 403):
                self._set_state(False, f"Login rejected (HTTP {response.status_code})")
            else:
                self._set_state(False, message)
            raise RuntimeError(message)
        return response.json()

    def _set_state(self, connected, error=None):
        self.connected = connected
        self.last_error = error

    def status_text(self):
        """One-line human-readable connection status for the GUI."""
        if not self.is_configured():
            return t("nc_not_configured")
        if self.connected is None:
            return t("nc_connecting")
        if not self.connected:
            return t("nc_not_connected", error=(self.last_error or t("nc_unknown_error"))[:90])
        text = t("nc_connected", title=self.table_title, table_id=self.table_id)
        if self.table_info.get("isShared"):
            owner = self.table_info.get("ownerDisplayName") or self.table_info.get("ownership")
            text += t("nc_shared_by", owner=owner)
        if self.last_sync_at:
            text += t("nc_last_upload", time=self.last_sync_at.strftime("%H:%M:%S"))
        return text

    def _write_id_to_env(self, new_id):
        """Persistently writes the Table ID back to the .env file."""
        from paths import ENV_PATH as env_path

        if not os.path.exists(env_path):
            open(env_path, "a").close()

        with open(env_path) as f:
            lines = f.readlines()

        updated = False
        with open(env_path, "w") as f:
            for line in lines:
                if line.startswith("NEXTCLOUD_TABLE_ID="):
                    f.write(f'NEXTCLOUD_TABLE_ID="{new_id}"\n')
                    updated = True
                else:
                    f.write(line)

            # If the variable didn't exist yet, append it to the end
            if not updated:
                if lines and not lines[-1].endswith("\n"):
                    f.write("\n")
                f.write(f'NEXTCLOUD_TABLE_ID="{new_id}"\n')

        os.environ["NEXTCLOUD_TABLE_ID"] = str(new_id)
        print(f'[C] Saved NEXTCLOUD_TABLE_ID="{new_id}" to {env_path}.')

    # ---------------------------------------------------------------
    # Table / column provisioning
    # ---------------------------------------------------------------

    def _find_table(self):
        """Returns the ID of the target table, or None if none exists yet.

        Several users can see several tables with the same title: their own
        and ones shared with them. All of them must end up writing into the
        SAME table, so the choice is deterministic: the OLDEST table with the
        title that this account may write to. Once the first user's table is
        shared with the others, every installation picks that one.

        NEXTCLOUD_TABLE_ID only caches the result; it is never trusted on its
        own (an ID from another server can belong to a foreign table)."""
        tables = self._request("GET", "/tables")
        by_id = {str(t.get("id")): t for t in tables}
        if self.table_id and by_id.get(self.table_id, {}).get("title") != self.table_title:
            reason = (
                "does not exist on this server"
                if self.table_id not in by_id
                else f"is the table {by_id[self.table_id].get('title')!r}"
            )
            print(f"[!] Stored NEXTCLOUD_TABLE_ID={self.table_id} {reason} -- ignoring it.")

        candidates = [
            tbl
            for tbl in tables
            if tbl.get("title") == self.table_title and not tbl.get("archived")
        ]
        writable = [tbl for tbl in candidates if _can_write(tbl)]
        for tbl in candidates:
            if tbl not in writable:
                print(
                    f"[!] Table '{self.table_title}' (ID {tbl.get('id')}) shared by "
                    f"{tbl.get('ownerDisplayName') or tbl.get('ownership')} is read-only for "
                    f"this account -- ask the owner to allow creating rows."
                )
        if not writable:
            self.table_info = {}
            return None

        chosen = min(
            writable, key=lambda tbl: (tbl.get("createdAt") or "", int(tbl.get("id") or 0))
        )
        others = sorted(str(tbl.get("id")) for tbl in writable if tbl is not chosen)
        if others and others != self._reported_duplicates:
            owner = chosen.get("ownerDisplayName") or chosen.get("ownership")
            print(
                f"[i] Several tables named '{self.table_title}' are visible "
                f"(IDs {', '.join([str(chosen.get('id'))] + others)}). Using the oldest one, "
                f"ID {chosen.get('id')} of {owner}. Records are uploaded there; "
                f"the others can be deleted once they are obsolete."
            )
            self._reported_duplicates = others
        self.table_info = chosen
        return str(chosen.get("id"))

    def _ensure_shares(self):
        """Shares OUR table with NEXTCLOUD_SHARE_WITH (read + create rows only),
        so other users find and use it instead of creating their own.
        Runs once per session; failures are reported but not fatal."""
        if self._shares_checked or not self.share_targets:
            return
        if self.table_info.get("isShared"):
            self._shares_checked = True  # not our table -- the owner shares it
            return
        try:
            existing = {
                (str(s.get("receiverType")).lower(), str(s.get("receiver")))
                for s in self._request("GET", f"/tables/{self.table_id}/shares")
            }
            for receiver_type, receiver in self.share_targets:
                if (receiver_type, receiver) in existing:
                    continue
                self._request(
                    "POST",
                    f"/tables/{self.table_id}/shares",
                    json={
                        "receiver": receiver,
                        "receiverType": receiver_type,
                        "permissionRead": True,
                        "permissionCreate": True,
                        "permissionUpdate": False,
                        "permissionDelete": False,
                        "permissionManage": False,
                    },
                )
                print(f"[C] Shared table {self.table_id} with {receiver_type} '{receiver}'.")
            self._shares_checked = True
        except Exception as e:
            print(f"[!] Could not share the table with {self.share_targets}: {e}")
            self._shares_checked = True  # don't retry every minute; restart to retry

    def _create_table(self):
        created = self._request(
            "POST",
            "/tables",
            json={"title": self.table_title, "emoji": "⛽", "template": "custom"},
        )
        print(f"[C] Created Nextcloud table '{self.table_title}' (ID: {created['id']}).")
        return str(created["id"])

    def _ensure_columns(self):
        """Resolves column titles to IDs and creates any missing columns."""
        existing = self._request("GET", f"/tables/{self.table_id}/columns")
        self.column_ids = {c["title"]: c["id"] for c in existing}

        missing = [title for title in COLUMN_DEFINITIONS if title not in self.column_ids]
        manage = (self.table_info.get("onSharePermissions") or {}).get("manage")
        if missing and self.table_info.get("isShared") and not manage:
            owner = self.table_info.get("ownerDisplayName") or self.table_info.get("ownership")
            raise RuntimeError(
                f"Shared table is missing the column(s) {', '.join(missing)} and this "
                f"account may not add them. {owner} must start FluidTrack-Mini once "
                f"to complete the table."
            )

        for title, definition in COLUMN_DEFINITIONS.items():
            if title in self.column_ids:
                continue
            payload = {"title": title, "mandatory": False, **definition}
            column = self._request("POST", f"/tables/{self.table_id}/columns", json=payload)
            self.column_ids[title] = column["id"]
            print(f"[C] Created column '{title}' (ID: {column['id']}).")

    def verify_or_create_table(self, verbose=True):
        """Makes sure the target table and its columns exist on Nextcloud and
        resolves their IDs. If NEXTCLOUD_TABLE_ID is missing or stale, the
        table is looked up by title or created. Prints a clear error on failure.
        With verbose=False (periodic checks) only status changes are logged."""
        if not self.is_configured():
            if verbose:
                print("[i] Nextcloud credentials missing -- table setup skipped.")
            return False

        was_connected = self.connected
        if verbose:
            print(f"[C] Validating Nextcloud table '{self.table_title}'...")
        try:
            table_id = self._find_table()
            if table_id is None:
                table_id = self._create_table()
            if table_id != os.getenv("NEXTCLOUD_TABLE_ID", "").strip():
                self._write_id_to_env(table_id)
            if self.table_id and table_id != self.table_id:
                # Switched tables (e.g. now using a table shared by a colleague):
                # compare everything again so all local records get uploaded there.
                print(f"[C] Switching from table {self.table_id} to table {table_id}.")
                self.reconciled = False
                self.column_ids = {}
            self.table_id = table_id

            self._ensure_columns()
            self._ensure_shares()
            self._set_state(True)
            if verbose or was_connected is not True:
                print(f"[C] Nextcloud connected, table ready (ID: {self.table_id}).")
            return True
        except Exception as e:
            if self.connected is not False:
                # Errors not raised by _request (e.g. unexpected response format)
                self._set_state(False, str(e))
            if verbose or was_connected is not False:
                print(f"[!] Nextcloud connection/table setup failed: {e}")
            return False

    def check_connection(self):
        """Periodic health check: re-validates table and columns (re-creating
        or re-resolving them if they were deleted or the ID is missing)."""
        return self.verify_or_create_table(verbose=False)

    # ---------------------------------------------------------------
    # Row upload
    # ---------------------------------------------------------------

    @staticmethod
    def _format_datetime(timestamp):
        """Tables expects datetime values as 'YYYY-MM-DD HH:MM'."""
        try:
            return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return timestamp

    def get_uploaded_hashes(self):
        """Returns the set of record hashes already stored in the Nextcloud table."""
        hash_column = self.column_ids["Hash"]
        hashes = set()
        offset = 0
        while True:
            rows = self._request(
                "GET",
                f"/tables/{self.table_id}/rows",
                params={"limit": ROWS_PAGE_SIZE, "offset": offset},
            )
            for row in rows:
                for cell in row.get("data") or []:
                    if cell.get("columnId") == hash_column and cell.get("value"):
                        hashes.add(cell["value"])
            if len(rows) < ROWS_PAGE_SIZE:
                return hashes
            offset += ROWS_PAGE_SIZE

    def upload_row(self, station, timestamp, liters, operator, key_id, record_hash=None):
        """Uploads one transaction. Returns True only if Nextcloud confirmed it."""
        if not self.is_ready():
            # Table setup may have failed at startup (e.g. network down) -- retry it.
            if not self.verify_or_create_table() or not self.is_ready():
                print("[!] Sync skipped: Nextcloud table is not available.")
                return False

        values = {
            "Station": station,
            "Timestamp": self._format_datetime(timestamp),
            "Liters": liters,
            "Operator": operator,
            "KeyID": key_id,
            "Hash": record_hash or "",
        }
        data = {str(self.column_ids[title]): value for title, value in values.items()}

        try:
            self._request("POST", f"/tables/{self.table_id}/rows", json={"data": data})
            self._set_state(True)
            self.last_sync_at = datetime.now()
            return True
        except Exception as e:
            print(f"[!] Nextcloud upload failed: {e}")
            return False
