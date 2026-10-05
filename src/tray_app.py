#!/usr/bin/env python3
"""
FluidTrack-Mini: Desktop Control Window (tkinter)
-------------------------------------------------
A small cross-platform (Windows/Linux) control window that wraps the
existing polling logic from main.py. It replaces the former pystray tray
icon, which did not work reliably across desktop environments.

Note: tkinter has no native system-tray support. Instead, this is a compact
window that minimizes to the taskbar when closed via the window's X button.
Use the "Quit" button to actually exit.

Features:
  - Status indicator (green = running, grey = paused, amber = busy)
  - Toggle the runner (polling loop) on/off
  - Log in to Nextcloud via the browser (Login Flow v2)
  - Live log output of the polling loop
  - Quit the application

The hardware polling runs in a background thread; pausing just clears an
in-memory flag that thread checks between cycles. All GUI updates happen on
the tkinter main thread via a queue polled with root.after().

Requirements:
    tkinter ships with most Python installs. On some minimal Linux
    distros you may need:  sudo apt install python3-tk

Usage:
    uv run python src/tray_app.py
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, scrolledtext, simpledialog, ttk

import main as runtime
from i18n import t
from main import POLL_INTERVAL_SECONDS, initialize, poll_once, set_delete_key_after_sync
from nextcloud import NextcloudTablesSync
from nextcloud_login import poll_for_credentials, start_login_flow, write_env_values
from paths import ENV_PATH

APP_TITLE = "FluidTrack-Mini"
COLOR_ACTIVE = "#2ecc71"    # green
COLOR_INACTIVE = "#95a5a6"  # grey
COLOR_BUSY = "#f1c40f"      # amber, shown during login
COLOR_ERROR = "#e74c3c"     # red, Nextcloud configured but not reachable
MAX_LOG_LINES = 500
UI_POLL_MS = 200


class QueueWriter:
    """File-like object that forwards print() output into a queue, so the
    worker thread's console output can be shown in the GUI log. Still
    writes to the original stream (if any) so terminal output keeps working."""

    def __init__(self, q, original):
        self.q = q
        self.original = original

    def write(self, text):
        if text:
            self.q.put(("log", text))
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:
                pass

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass


class ControlApp:
    def __init__(self):
        self.ui_queue = queue.Queue()
        # PyInstaller windowed builds have sys.stdout = None; QueueWriter handles that.
        sys.stdout = QueueWriter(self.ui_queue, sys.stdout)
        sys.stderr = QueueWriter(self.ui_queue, sys.stderr)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("560x380")
        self.root.minsize(380, 260)
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self.runner_active = threading.Event()
        self.runner_active.set()  # start active by default
        self.stop_requested = threading.Event()
        self.login_in_progress = False

        self._build_ui()

        # --- Core runner state ---
        self.db, self.cloud, self.nextcloud_enabled = initialize()
        self._update_status()

        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()

        self.root.after(UI_POLL_MS, self._process_ui_queue)

    # ---------------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------------

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill="x")

        self.status_canvas = tk.Canvas(top, width=18, height=18, highlightthickness=0)
        self.status_dot = self.status_canvas.create_oval(2, 2, 16, 16, fill=COLOR_ACTIVE, outline="")
        self.status_canvas.pack(side="left")

        self.status_label = ttk.Label(top, text=t("starting"))
        self.status_label.pack(side="left", padx=(6, 0))

        cloud_row = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        cloud_row.pack(fill="x")

        self.cloud_canvas = tk.Canvas(cloud_row, width=18, height=18, highlightthickness=0)
        self.cloud_dot = self.cloud_canvas.create_oval(2, 2, 16, 16, fill=COLOR_INACTIVE, outline="")
        self.cloud_canvas.pack(side="left")

        self.nextcloud_label = ttk.Label(cloud_row, text=t("nc_prefix") + "...")
        self.nextcloud_label.pack(side="left", padx=(6, 0), fill="x", expand=True)

        buttons = ttk.Frame(self.root, padding=(10, 4))
        buttons.pack(fill="x")

        self.toggle_button = ttk.Button(buttons, text=t("pause_runner"), command=self._toggle_runner)
        self.toggle_button.pack(side="left")

        self.login_button = ttk.Button(
            buttons, text=t("login"), command=self._on_login_clicked
        )
        self.login_button.pack(side="left", padx=6)

        ttk.Button(buttons, text=t("quit"), command=self._quit).pack(side="right")

        options = ttk.Frame(self.root, padding=(10, 2))
        options.pack(fill="x")
        self.delete_after_sync_var = tk.BooleanVar(value=runtime.DELETE_KEY_AFTER_SYNC)
        ttk.Checkbutton(
            options,
            text=t("erase_after_sync"),
            variable=self.delete_after_sync_var,
            command=self._on_delete_after_sync_toggled,
        ).pack(side="left")

        self.log_text = scrolledtext.ScrolledText(
            self.root, height=12, state="disabled", wrap="word", font=("TkFixedFont", 9)
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(4, 10))

    def _update_status(self, busy_text=None):
        if busy_text:
            color, text = COLOR_BUSY, busy_text
        elif self.runner_active.is_set():
            color, text = COLOR_ACTIVE, t("runner_active", seconds=POLL_INTERVAL_SECONDS)
        else:
            color, text = COLOR_INACTIVE, t("runner_paused")
        self.status_canvas.itemconfigure(self.status_dot, fill=color)
        self.status_label.configure(text=text)
        self.toggle_button.configure(
            text=t("pause_runner") if self.runner_active.is_set() else t("resume_runner")
        )
        self.login_button.configure(state="disabled" if self.login_in_progress else "normal")
        self._update_cloud_status()

    def _update_cloud_status(self):
        cloud = getattr(self, "cloud", None)
        if cloud is None:
            return
        if not cloud.is_configured():
            color = COLOR_INACTIVE
        elif cloud.connected is None:
            color = COLOR_BUSY
        elif cloud.connected:
            color = COLOR_ACTIVE
        else:
            color = COLOR_ERROR
        self.cloud_canvas.itemconfigure(self.cloud_dot, fill=color)
        self.nextcloud_label.configure(text=t("nc_prefix") + cloud.status_text())
        self.login_button.configure(
            text=t("relogin") if cloud.connected else t("login")
        )

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        # Trim old lines so the widget doesn't grow forever.
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _process_ui_queue(self):
        """Runs on the tkinter main thread. Applies everything background
        threads have requested (log lines, status refreshes)."""
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self._update_status(busy_text=payload)
        except queue.Empty:
            pass
        # Connection state is changed by the worker thread; mirror it here.
        self._update_cloud_status()
        if not self.stop_requested.is_set():
            self.root.after(UI_POLL_MS, self._process_ui_queue)

    # ---------------------------------------------------------------
    # Actions
    # ---------------------------------------------------------------

    def _toggle_runner(self):
        if self.runner_active.is_set():
            self.runner_active.clear()
            print("[ui] Runner paused.")
        else:
            self.runner_active.set()
            print("[ui] Runner resumed.")
        self._update_status()

    def _on_delete_after_sync_toggled(self):
        enabled = self.delete_after_sync_var.get()
        if enabled:
            confirmed = messagebox.askyesno(
                APP_TITLE,
                t("erase_confirm"),
                icon="warning",
                parent=self.root,
            )
            if not confirmed:
                self.delete_after_sync_var.set(False)
                return

        set_delete_key_after_sync(enabled)
        try:
            write_env_values(ENV_PATH, {"DELETE_KEY_AFTER_SYNC": "true" if enabled else "false"})
        except OSError as e:
            print(f"[!] Could not save the setting to .env: {e}")
        print(
            "[ui] Erase after sync: "
            + ("ENABLED -- keys are erased once fully synced." if enabled else "disabled.")
        )

    def _on_login_clicked(self):
        # The URL dialog must run on the main thread; the network/browser
        # wait runs in a worker thread so the window stays responsive.
        url = simpledialog.askstring(APP_TITLE, t("enter_url"), parent=self.root)
        if not url:
            return
        self.login_in_progress = True
        self._update_status(busy_text=t("waiting_login"))
        threading.Thread(target=self._do_nextcloud_login, args=(url.strip(),), daemon=True).start()

    def _do_nextcloud_login(self, url):
        print("[ui] Opening your browser to log in to Nextcloud...")
        try:
            login_url, poll_token, poll_endpoint = start_login_flow(url)
            webbrowser.open(login_url)
            credentials = poll_for_credentials(poll_endpoint, poll_token)

            write_env_values(
                ENV_PATH,
                {
                    "NEXTCLOUD_URL": credentials["server"],
                    "NEXTCLOUD_USER": credentials["loginName"],
                    "NEXTCLOUD_APP_TOKEN": credentials["appPassword"],
                },
            )

            # Reload the Nextcloud client so the new credentials take effect
            # immediately, without restarting the whole application.
            from dotenv import load_dotenv

            load_dotenv(ENV_PATH, override=True)
            cloud = NextcloudTablesSync()
            cloud.verify_or_create_table()
            self.cloud = cloud
            self.nextcloud_enabled = cloud.is_configured()

            print(f"[ui] Logged in to Nextcloud as {credentials['loginName']}.")
        except Exception as e:
            print(f"[!] Nextcloud login failed: {e}")
        finally:
            self.login_in_progress = False
            self.ui_queue.put(("status", None))

    def _on_window_close(self):
        # tkinter has no tray icon, so closing the window just minimizes it
        # to the taskbar and keeps the runner going.
        self.root.iconify()

    def _quit(self):
        self.stop_requested.set()
        self.root.destroy()

    # ---------------------------------------------------------------
    # Background worker
    # ---------------------------------------------------------------

    def _run_loop(self):
        while not self.stop_requested.is_set():
            if self.runner_active.is_set():
                try:
                    poll_once(self.db, self.cloud, self.nextcloud_enabled)
                except Exception as e:
                    print(f"[!] Error during poll cycle: {e}")
            # Sleep in small steps so Quit/toggle react quickly instead of
            # waiting out a full POLL_INTERVAL_SECONDS.
            slept = 0.0
            while slept < POLL_INTERVAL_SECONDS and not self.stop_requested.is_set():
                time.sleep(0.2)
                slept += 0.2

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.stop_requested.set()


if __name__ == "__main__":
    ControlApp().run()
