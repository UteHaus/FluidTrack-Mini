"""
GUI translations (English/German).

The language comes from UI_LANGUAGE in .env ("de" or "en"). If it is empty,
the operating system's UI language is used: German for any German locale,
English otherwise. Log output stays English.
"""

import os
import sys

STRINGS = {
    "en": {
        "starting": "Starting...",
        "runner_active": "Runner active (every {seconds}s)",
        "runner_paused": "Runner paused",
        "pause_runner": "Pause runner",
        "resume_runner": "Resume runner",
        "login": "Log in to Nextcloud...",
        "relogin": "Re-login to Nextcloud...",
        "quit": "Quit",
        "key_reading": "Reading key {rom} -- do not remove it!",
        "key_synced": "Key {rom} read -- everything synced",
        "key_pending": "Key {rom} read -- stored locally, Nextcloud sync pending",
        "key_error": "Key {rom} could not be read -- retrying",
        "erase_after_sync": "Erase key automatically after successful sync",
        "erase_confirm": (
            "Erase the key automatically after a successful sync?\n\n"
            "The key is only erased when EVERY transaction on it is stored "
            "locally and, if Nextcloud is configured, confirmed uploaded. "
            "Erased data cannot be restored from the key.\n\n"
            "Please test this with a non-critical key first."
        ),
        "enter_url": "Enter your Nextcloud URL:",
        "waiting_login": "Waiting for Nextcloud login in browser...",
        "login_failed": "Nextcloud login failed:\n\n{error}",
        "login_timeout": "The login was not completed in the browser in time.",
        "browser_failed": "The browser could not be opened. Open this link to log in:",
        "copy_link": "Copy link",
        "link_copied": "Copied",
        "close": "Close",
        "nc_prefix": "Nextcloud: ",
        "nc_not_configured": "not configured",
        "nc_connecting": "connecting...",
        "nc_not_connected": "NOT connected - {error}",
        "nc_unknown_error": "unknown error",
        "nc_connected": "connected - table '{title}' (ID {table_id})",
        "nc_last_upload": ", last upload {time}",
        "nc_shared_by": ", shared by {owner}",
        "nc_uploading": " -- uploading...",
        "already_running": "{error}",
    },
    "de": {
        "starting": "Startet...",
        "runner_active": "Runner aktiv (alle {seconds} s)",
        "runner_paused": "Runner pausiert",
        "pause_runner": "Runner pausieren",
        "resume_runner": "Runner fortsetzen",
        "login": "Bei Nextcloud anmelden...",
        "relogin": "Erneut bei Nextcloud anmelden...",
        "quit": "Beenden",
        "key_reading": "Schlüssel {rom} wird gelesen -- nicht abnehmen!",
        "key_synced": "Schlüssel {rom} gelesen -- alles synchronisiert",
        "key_pending": "Schlüssel {rom} gelesen -- lokal gespeichert, Synchronisierung ausstehend",
        "key_error": "Schlüssel {rom} konnte nicht gelesen werden -- neuer Versuch",
        "erase_after_sync": "Schlüssel nach erfolgreicher Synchronisierung automatisch löschen",
        "erase_confirm": (
            "Schlüssel nach erfolgreicher Synchronisierung automatisch löschen?\n\n"
            "Der Schlüssel wird nur gelöscht, wenn JEDER Tankvorgang darauf lokal "
            "gespeichert und, falls Nextcloud eingerichtet ist, nachweislich "
            "hochgeladen ist. Gelöschte Daten lassen sich vom Schlüssel nicht "
            "wiederherstellen.\n\n"
            "Bitte zuerst mit einem unkritischen Schlüssel testen."
        ),
        "enter_url": "Nextcloud-Adresse eingeben:",
        "waiting_login": "Warte auf Nextcloud-Anmeldung im Browser...",
        "login_failed": "Nextcloud-Anmeldung fehlgeschlagen:\n\n{error}",
        "login_timeout": "Die Anmeldung wurde im Browser nicht rechtzeitig abgeschlossen.",
        "browser_failed": (
            "Der Browser konnte nicht geöffnet werden. Zum Anmelden diesen Link öffnen:"
        ),
        "copy_link": "Link kopieren",
        "link_copied": "Kopiert",
        "close": "Schließen",
        "nc_prefix": "Nextcloud: ",
        "nc_not_configured": "nicht eingerichtet",
        "nc_connecting": "verbinde...",
        "nc_not_connected": "NICHT verbunden - {error}",
        "nc_unknown_error": "unbekannter Fehler",
        "nc_connected": "verbunden - Tabelle '{title}' (ID {table_id})",
        "nc_last_upload": ", letzter Upload {time}",
        "nc_shared_by": ", geteilt von {owner}",
        "nc_uploading": " -- lade hoch...",
        "already_running": (
            "FluidTrack-Mini läuft bereits mit dieser Datenbank. "
            "Bitte zuerst die andere Instanz beenden.\n\n{error}"
        ),
    },
}


def _system_language():
    if sys.platform == "win32":
        try:
            import ctypes

            # Primary language ID 0x07 = German (any region).
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "de" if lang_id & 0x3FF == 0x07 else "en"
        except Exception:
            return "en"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "de" if value.lower().startswith("de") else "en"
    return "en"


def language():
    """Read on every call, so a .env reloaded at runtime takes effect."""
    configured = os.getenv("UI_LANGUAGE", "").strip().lower()
    return configured if configured in STRINGS else _system_language()


def t(key, **kwargs):
    text = STRINGS[language()].get(key) or STRINGS["en"][key]
    return text.format(**kwargs) if kwargs else text
