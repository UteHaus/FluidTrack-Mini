#!/usr/bin/env python3
"""
FluidTrack-Mini: iButton (DS1996) Rohdaten-Inspektor
-----------------------------------------------------
Dieses Skript dient AUSSCHLIESSLICH dazu, die Rohdaten eines PIUSI-Schluessels
(DS1996 iButton ueber DS9490R) sichtbar zu machen, BEVOR ein Binaer-Decoder
geschrieben wird. Es interpretiert die Daten nicht, sondern zeigt sie als
Hexdump + ASCII an und speichert optional eine .bin-Kopie fuer die spaetere
Analyse.

Nutzung:
    python inspect_key.py
    python inspect_key.py --save        # speichert zusaetzlich eine .bin Datei
    python inspect_key.py --file rw     # erzwingt ein bestimmtes Interface-File
"""

import argparse
import datetime
import os
import platform
import sys

W1_DEVICES_DIR = "/sys/bus/w1/devices/"
USB_VENDOR_ID = 0x04FA
USB_PRODUCT_ID = 0x2490

# Reihenfolge, in der Interface-Dateien probiert werden (wie in main.py)
CANDIDATE_FILES = ["rw", "memory", "eeprom", "w1_slave"]


def find_w1_master_bus():
    if not os.path.isdir(W1_DEVICES_DIR):
        return None
    candidates = [d for d in os.listdir(W1_DEVICES_DIR) if d.startswith("w1_bus_master")]
    return candidates[0] if candidates else None


def find_keys_on_bus(master_bus):
    """Liest die Liste aktiver 1-Wire Geraete vom Master-Bus."""
    slaves_file = os.path.join(W1_DEVICES_DIR, master_bus, "w1_master_slaves")
    if not os.path.exists(slaves_file):
        return []
    with open(slaves_file) as f:
        lines = [line.strip() for line in f if line.strip()]
    return [line for line in lines if "not found" not in line]


def read_key_files(key_id, only_file=None):
    """
    Liest ALLE verfuegbaren Interface-Dateien eines Schluessels (nicht nur die erste),
    damit man vergleichen kann, was jede Datei liefert.
    Gibt ein dict {dateiname: bytes} zurueck.
    """
    key_dir = os.path.join(W1_DEVICES_DIR, key_id)
    results = {}
    files_to_try = [only_file] if only_file else CANDIDATE_FILES

    for filename in files_to_try:
        path = os.path.join(key_dir, filename)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
            results[filename] = data
        except OSError as e:
            print(f"    [!] Konnte '{filename}' nicht lesen: {e}")

    return results


def mock_windows_read():
    """Windows-Hinweis: echtes 1-Wire-Auslesen ist hier (noch) nicht implementiert."""
    try:
        import usb.core

        dev = usb.core.find(idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID)
        if dev is not None:
            print("[!] DS9490R per USB erkannt, aber echtes 1-Wire-Auslesen ist unter")
            print("    Windows in diesem Inspektions-Skript noch nicht implementiert")
            print("    (das erfordert das pyusb-1-Wire-Protokoll, nicht nur USB-Erkennung).")
            print("    -> Fuehre dieses Skript stattdessen auf Linux aus (w1 Kernel-Subsystem).")
        else:
            print("[-] Kein DS9490R Adapter per USB gefunden.")
    except ImportError:
        print("[-] Modul 'pyusb' nicht installiert. `pip install pyusb`.")
    return {}


def hexdump(data, width=16):
    """Klassischer hexdump -C Stil: Offset | Hex-Bytes | ASCII."""
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="PIUSI iButton Rohdaten-Inspektor")
    parser.add_argument(
        "--save", action="store_true", help="Rohdaten zusaetzlich als .bin Datei speichern"
    )
    parser.add_argument(
        "--file", choices=CANDIDATE_FILES, help="Nur eine bestimmte Interface-Datei lesen"
    )
    args = parser.parse_args()

    print(f"[+] Starte Rohdaten-Inspektion auf {platform.system()}...\n")

    current_os = platform.system()
    results_by_key = {}

    if current_os != "Linux":
        mock_windows_read()
        sys.exit(1)

    master_bus = find_w1_master_bus()
    if not master_bus:
        print(f"[-] Kein 1-Wire Master-Bus unter {W1_DEVICES_DIR} gefunden.")
        print("    -> Ist der DS9490R eingesteckt und das Kernelmodul (w1-gpio/ds2490) geladen?")
        sys.exit(1)

    print(f"[+] Master-Bus gefunden: {master_bus}")

    keys = find_keys_on_bus(master_bus)
    if not keys:
        print("[-] Kein Schluessel auf dem Bus erkannt. Bitte Key auflegen und erneut versuchen.")
        sys.exit(1)

    print(f"[+] {len(keys)} Geraet(e) erkannt: {', '.join(keys)}\n")

    for key_id in keys:
        print(f"=== Schluessel: {key_id} ===")
        file_data = read_key_files(key_id, only_file=args.file)
        if not file_data:
            print(
                "    [!] Keine der erwarteten Interface-Dateien (rw/memory/eeprom/w1_slave) lesbar."
            )
        results_by_key[key_id] = file_data

    # Ausgabe + optionales Speichern
    for key_id, file_data in results_by_key.items():
        for filename, data in file_data.items():
            print(f"\n--- {key_id} :: {filename}  ({len(data)} Bytes) ---")
            print(hexdump(data))

            if args.save and data:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_key = key_id.replace("/", "_")
                out_name = f"dump_{safe_key}_{filename}_{timestamp}.bin"
                with open(out_name, "wb") as out_f:
                    out_f.write(data)
                print(f"[SAVE] Rohdaten gespeichert als: {out_name}")

    print("\n[OK] Inspektion abgeschlossen. Naechster Schritt: Byte-Struktur der")
    print("     interessantesten Datei (meist 'rw' oder 'memory') manuell analysieren,")
    print("     bevor ein fester Binaer-Decoder geschrieben wird.")


if __name__ == "__main__":
    main()
