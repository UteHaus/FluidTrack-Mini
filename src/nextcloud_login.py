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
import sys
import time
import webbrowser

import requests

POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 300  # 5 minutes to complete the login in the browser


def start_login_flow(nextcloud_url: str):
    """Requests a new login flow. Does NOT require prior authentication."""
    endpoint = f"{nextcloud_url.rstrip('/')}/index.php/login/v2"
    response = requests.post(endpoint, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data["login"], data["poll"]["token"], data["poll"]["endpoint"]


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
        with open(env_path, "r") as f:
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
    except requests.RequestException as e:
        print(f"[-] Could not start the login flow: {e}")
        sys.exit(1)

    print(f"[+] Opening browser to log in: {login_url}")
    print("    (If no browser opens automatically, please open the link manually.)")
    webbrowser.open(login_url)

    print(f"[...] Waiting for confirmation in the browser (up to {POLL_TIMEOUT_SECONDS // 60} minutes)...")
    try:
        credentials = poll_for_credentials(poll_endpoint, poll_token)
    except (requests.RequestException, TimeoutError) as e:
        print(f"[-] Login failed: {e}")
        sys.exit(1)

    write_env_values(
        env_path,
        {
            "NEXTCLOUD_URL": credentials["server"],
            "NEXTCLOUD_USER": credentials["loginName"],
            "NEXTCLOUD_APP_TOKEN": credentials["appPassword"],
        },
    )

    print(f"[OK] Login successful! Credentials were saved to {env_path}.")
    print(f"     Logged in as: {credentials['loginName']} on {credentials['server']}")
    print("     Next step: run 'uv run python main.py'.")


if __name__ == "__main__":
    main()
