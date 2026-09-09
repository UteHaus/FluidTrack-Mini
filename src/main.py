import os
import platform
import sqlite3
import sys
import time
from dotenv import load_dotenv

# Ermittle den Ordner, in dem dieses Skript liegt
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

# `.env`-Datei explizit aus dem Skript-Ordner laden
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
else:
    print("[⚠️] Hinweis: Keine .env-Datei gefunden. Nutze interne Standardwerte.")

# --- KONFIGURATION AUS .ENV ---
DB_PATH = os.getenv("DB_PATH", os.path.join(SCRIPT_DIR, "FluidTrack.db"))
DB_TABLE = os.getenv("DB_TABLE_NAME", "FuelTransactions")

W1_DEVICES_DIR = "/sys/bus/w1/devices/"  # Nur für Linux relevant
USB_VENDOR_ID = 0x04FA
USB_PRODUCT_ID = 0x2490


def init_database():
    """Prüft, ob die Tabelle existiert. Wenn nicht, wird sie mit Testdaten erstellt."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Erstelle die Tabelle, falls sie fehlt
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {DB_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                RegNum TEXT NOT NULL,
                DataOra TEXT NOT NULL,
                Printed INTEGER DEFAULT 0
            )
        """)
        conn.commit()

        # Prüfen, ob bereits Daten vorhanden sind
        cursor.execute(f"SELECT COUNT(*) FROM {DB_TABLE}")
        if cursor.fetchone()[0] == 0:
            print(f"[⚙️] Erstelle Test-Tankdaten in '{DB_TABLE}'...")
            test_data = [
                ("Station_01", "2026-09-09 14:23:11", 0),
                ("Station_02", "2026-09-09 15:05:42", 0),
            ]
            cursor.executemany(
                f"INSERT INTO {DB_TABLE} (RegNum, DataOra, Printed) VALUES (?, ?, ?)",
                test_data,
            )
            conn.commit()
            print("[✅] Testdaten erfolgreich eingefügt.")

    except Exception as e:
        print(f"[-] Fehler bei der Datenbank-Initialisierung: {e}")
    finally:
        conn.close()


def detect_and_read_key():
    """Prüft plattformabhängig, ob ein DS9490R verbunden ist."""
    os_type = platform.system()

    # --- LINUX LOGIK ---
    if os_type == "Linux":
        if not os.path.exists(W1_DEVICES_DIR):
            return None, None

        master_bus = [
            d for d in os.listdir(W1_DEVICES_DIR) if d.startswith("w1_bus_master")
        ]
        if not master_bus:
            return None, None

        slaves_file = os.path.join(W1_DEVICES_DIR, master_bus[0], "w1_master_slaves")
        if os.path.exists(slaves_file):
            with open(slaves_file, "r") as f:
                active_keys = [
                    line.strip()
                    for line in f.readlines()
                    if line.strip() and "not found" not in line
                ]

                if active_keys:
                    key_id = active_keys[0]
                    key_dir = os.path.join(W1_DEVICES_DIR, key_id)

                    possible_data_files = ["rw", "memory", "eeprom", "w1_slave"]
                    data_path = None

                    for filename in possible_data_files:
                        test_path = os.path.join(key_dir, filename)
                        if os.path.exists(test_path):
                            data_path = test_path
                            break

                    if not data_path:
                        return key_id, None

                    try:
                        with open(data_path, "rb") as f:
                            return key_id, f.read()
                    except IOError:
                        return key_id, None

        return None, None

    # --- WINDOWS LOGIK ---
    elif os_type == "Windows":
        try:
            import usb.core
        except ImportError:
            return None, None

        dev = usb.core.find(idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID)
        if dev is None:
            return None, None

        # Simulation für Windows-Tests
        mock_key_id = "0c-0000001db780"
        mock_raw_data = b"Tankdaten_Vom_Schluessel"
        return mock_key_id, mock_raw_data

    return None, None


def process_tank_data():
    """Hauptroutine: Prüft auf Schlüssel und gleicht mit der SQLite-Datenbank ab."""
    key_id, raw_data = detect_and_read_key()

    if not key_id:
        print("[-] Kein Schlüssel (iButton) auf dem DS9490R-Lesegerät erkannt.")
        return

    print(f"[>] Erkannter Manager-Schlüssel ID: {key_id}")

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
    except Exception as e:
        print(f"[-] Fehler beim Öffnen der Datenbank {DB_PATH}: {e}")
        return

    try:
        query = (
            f"SELECT id, RegNum FROM {DB_TABLE} WHERE Printed IS NOT 1 ORDER BY DataOra"
        )
        cursor.execute(query)
        rows = cursor.fetchall()

        if not rows:
            print(f"[+] Keine offenen Tankdaten-Datensätze in Tabelle '{DB_TABLE}'.")
            return

        for row in rows:
            record_id = row[0]
            reg_num = row[1]

            if raw_data:
                print(f"[<] Daten für Station {reg_num} erfolgreich verarbeitet.")
                update_query = f"UPDATE {DB_TABLE} SET Printed = 1 WHERE id = ?"
                cursor.execute(update_query, (record_id,))
                conn.commit()
                print(f"[+] Datensatz ID {record_id} aktualisiert.")

    except Exception as e:
        print(f"[-] Fehler in der Kommunikationsschleife: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"[+] Starte Überwachung auf {platform.system()}...")
    print(f"[i] Nutze Datenbank: {DB_PATH} | Tabelle: {DB_TABLE}")

    # Datenbank vor dem Start prüfen und ggf. vorbereiten
    init_database()

    try:
        while True:
            process_tank_data()
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n👋 Überwachung beendet.")
