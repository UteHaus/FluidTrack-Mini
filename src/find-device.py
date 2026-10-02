#!/usr/bin/env python3
"""
FluidTrack-Mini: adapter diagnostics (Linux and Windows)

Checks step by step whether the DS9490R USB adapter can be opened and which
1-Wire devices are on the bus -- directly over USB, no kernel driver needed.

Usage:
    uv run python find-device.py
"""

import sys

from ds9490_direct import DS9490, DS2490Error, rom_bytes_to_id

FAMILY_NAMES = {
    0x0C: "DS1996 fuel key (64 kbit NV-RAM)",
    0x01: "DS1990/DS2401 ID chip",
    0x81: "DS1420 ID chip (built into the DS9490R)",
}


def main():
    print(f"[i] Platform: {sys.platform}")
    try:
        with DS9490() as ds:
            print("[OK] DS9490R opened over USB.")
            roms = ds.search_roms()
    except DS2490Error as e:
        print(f"[!] {e}")
        return 1

    if not roms:
        print("[-] No 1-Wire devices found on the bus.")
        return 1

    print(f"[OK] {len(roms)} device(s) on the 1-Wire bus:")
    for rom in roms:
        name = FAMILY_NAMES.get(rom[0], "unknown family")
        print(f"     {rom_bytes_to_id(rom)}  {name}")
    if not any(rom[0] == 0x0C for rom in roms):
        print("[-] No DS1996 key detected -- place the key on the reader.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
