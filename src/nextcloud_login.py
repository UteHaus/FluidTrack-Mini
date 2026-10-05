#!/usr/bin/env python3
"""
FluidTrack-Mini: Nextcloud login via browser (Login Flow v2)
----------------------------------------------------------------
Instead of manually generating an app password in Nextcloud's personal
security settings and pasting it into .env, this script uses the same
"Login Flow v2" that the official Nextcloud Desktop Client uses:

1. Requests a login flow from the Nextcloud instance.
2. Opens the login page in the default browser.
3. The user logs in normally there (including 2FA if enabled) and
   confirms access for "FluidTrack-Mini".
4. The script polls in the background until the login is complete, and in
   the process automatically receives an app password (the login/password
   are NEVER entered or stored directly by this script).
5. Server URL, username, and app password are written into the .env file
   automatically (existing values are updated).

Usage:
    uv run python nextcloud_login.py https://my-nextcloud.example.com
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 300  # 5 minutes to complete the login in the browser


class NextcloudLoginError(RuntimeError):
    """Login flow could not be started -- message is meant for the user."""


LOGIN_FLOW_PATH = "/index.php/login/v2"

# Path segments that mark where a URL copied from the browser stops being the
# Nextcloud base path (e.g. https://cloud.example.com/nc/apps/files/ -> .../nc).
_NEXTCLOUD_PATH_MARKERS = ("index.php", "apps", "login", "remote.php", "ocs", "s", "f", "settings")


def normalize_nextcloud_url(raw: str) -> str:
    """Turns whatever the user typed or pasted into the Nextcloud base URL:
    adds https:// if the scheme is missing, drops query/fragment and cuts
    browser paths like /apps/files/ or /index.php/... while keeping a
    sub-directory installation (https://example.com/nextcloud)."""
    url = (raw or "").strip()
    if not url:
        raise NextcloudLoginError("Please enter the address of your Nextcloud.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise NextcloudLoginError(f"Not a valid web address: {raw!r}")

    segments = [seg for seg in parts.path.split("/") if seg]
    base = []
    for seg in segments:
        if seg.lower() in _NEXTCLOUD_PATH_MARKERS:
            break
        base.append(seg)
    path = "/" + "/".join(base) if base else ""
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def start_login_flow(nextcloud_url: str):
    """Requests a new login flow. Does NOT require prior authentication.

    Redirects are followed manually: requests would turn the POST into a GET
    on a 301/302 (e.g. http -> https), which Nextcloud answers with 405."""
    base = normalize_nextcloud_url(nextcloud_url)
    endpoint = base + LOGIN_FLOW_PATH
    for _ in range(5):
        try:
            response = requests.post(endpoint, timeout=10, allow_redirects=False)
        except requests.exceptions.SSLError as e:
            raise NextcloudLoginError(f"Secure connection to {base} failed: {e}") from e
        except requests.RequestException as e:
            raise NextcloudLoginError(f"Cannot reach {base}: {e}") from e
        if response.is_redirect or response.is_permanent_redirect:
            endpoint = urljoin(endpoint, response.headers["Location"])
            continue
        break
    else:
        raise NextcloudLoginError(f"Too many redirects from {base}.")

    if response.status_code in (404, 405):
        raise NextcloudLoginError(
            f"No Nextcloud found at {base} (HTTP {response.status_code}). Please check the address."
        )
    if not response.ok:
        raise NextcloudLoginError(f"{base} answered with HTTP {response.status_code}.")
    try:
        data = response.json()
        return data["login"], data["poll"]["token"], data["poll"]["endpoint"]
    except (ValueError, KeyError, TypeError) as e:
        raise NextcloudLoginError(f"{base} does not look like a Nextcloud server.") from e


def open_browser(url: str) -> bool:
    """Opens the URL in the default browser. Returns False if that failed.

    In a PyInstaller build on Linux, LD_LIBRARY_PATH points to the bundled
    libraries; a browser started with it can crash silently. The original
    environment (saved by PyInstaller as *_ORIG) is restored for the launch."""
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        env = dict(os.environ)
        for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
            original = env.pop(var + "_ORIG", None)
            if original is not None:
                env[var] = original
            else:
                env.pop(var, None)
        for opener in ("xdg-open", "gio", "sensible-browser"):
            path = shutil.which(opener)
            if not path:
                continue
            cmd = [path, "open", url] if opener == "gio" else [path, url]
            try:
                subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return True
            except OSError:
                continue
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def poll_for_credentials(poll_endpoint: str, poll_token: str):
    """
    Polls the endpoint until the user has completed the login in the browser.
    Nextcloud returns 404 while not yet confirmed -- that's expected and not
    an error; only other status codes are treated as a real failure.
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        response = requests.post(poll_endpoint, data={"token": poll_token}, timeout=10)
        if response.status_code == 200:
            return response.json()  # {"server":..., "loginName":..., "appPassword":...}
        if response.status_code != 404:
            response.raise_for_status()
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"Login was not completed in the browser within {POLL_TIMEOUT_SECONDS // 60} minutes."
    )


def write_env_values(env_path: str, values: dict):
    """Updates existing variables in .env or appends new ones -- mirrors the
    existing pattern in nextcloud.py (_write_id_to_env)."""
    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    remaining = dict(values)
    with open(env_path, "w") as f:
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line else None
            if key in remaining:
                f.write(f'{key}="{remaining.pop(key)}"\n')
            else:
                f.write(line)
        for key, value in remaining.items():
            f.write(f'{key}="{value}"\n')


def credentials_to_env(credentials: dict) -> dict:
    """The .env values to store after a successful login. NEXTCLOUD_TABLE_ID
    is cleared: a table ID is only valid on the server it came from -- on
    another Nextcloud the same number can belong to a foreign table. The app
    looks the table up again by its title (or creates it)."""
    return {
        "NEXTCLOUD_URL": credentials["server"],
        "NEXTCLOUD_USER": credentials["loginName"],
        "NEXTCLOUD_APP_TOKEN": credentials["appPassword"],
        "NEXTCLOUD_TABLE_ID": "",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Nextcloud login via browser (like the Desktop Client)"
    )
    parser.add_argument("nextcloud_url", help="e.g. https://my-nextcloud.example.com")
    args = parser.parse_args()

    from paths import ENV_PATH as env_path

    print("[+] Starting Nextcloud login flow...")
    try:
        login_url, poll_token, poll_endpoint = start_login_flow(args.nextcloud_url)
    except NextcloudLoginError as e:
        print(f"[-] Could not start the login flow: {e}")
        sys.exit(1)

    print(f"[+] Opening browser to log in: {login_url}")
    if not open_browser(login_url):
        print("    No browser could be opened -- please open the link above manually.")

    timeout_minutes = POLL_TIMEOUT_SECONDS // 60
    print(f"[...] Waiting for confirmation in the browser (up to {timeout_minutes} minutes)...")
    try:
        credentials = poll_for_credentials(poll_endpoint, poll_token)
    except (requests.RequestException, TimeoutError) as e:
        print(f"[-] Login failed: {e}")
        sys.exit(1)

    write_env_values(env_path, credentials_to_env(credentials))

    print(f"[OK] Login successful! Credentials were saved to {env_path}.")
    print(f"     Logged in as: {credentials['loginName']} on {credentials['server']}")
    print("     Next step: run 'uv run python main.py'.")


if __name__ == "__main__":
    main()
