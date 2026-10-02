#!/usr/bin/env python3
"""
FluidTrack-Mini: Direct USB access to the DS9490R (DS2490 chip)
------------------------------------------------------------------
Implements the publicly documented DS2490 USB protocol directly via pyusb --
independent of the Linux kernel's w1 subsystem (which has no dedicated slave
driver for the DS1996 memory family) and independent of the proprietary
TMEX/IBFS32 Windows drivers.

The command constants come from the public Maxim/Analog Devices DS2490
datasheet and match the ones also used by the GPL-licensed Linux kernel
driver (drivers/w1/masters/ds2490.c) -- reimplemented here from scratch in
Python (no code copied).

Requirements:
    pip install pyusb
    Linux: the kernel driver is detached automatically on startup (see
           detach_kernel_driver below). May need root or a udev rule for
           the DS9490R (idVendor=04fa, idProduct=2490).
    Windows: pyusb needs a libusb-compatible driver for the device (e.g.
           switch it to "WinUSB" via Zadig) -- the regular TMEX driver is
           NOT usable by pyusb directly.

Usage:
    python ds9490_direct.py 0c-0000000000ab
    python ds9490_direct.py 0c-0000000000ab --length 8192 --start 0
"""

import argparse
import datetime
import time

import usb.core
import usb.util

VENDOR_ID = 0x04FA
PRODUCT_ID = 0x2490

# --- DS2490 vendor command constants (from the public datasheet) ---
VENDOR_REQUEST_TYPE = 0x40  # bmRequestType for all DS2490 vendor commands

CONTROL_CMD = 0x00
COMM_CMD = 0x01
MODE_CMD = 0x02

CTL_RESET_DEVICE = 0x0000

MOD_PULSE_EN = 0x0000
PULSE_SPUE = 0x02

COMM_IM = 0x0001  # "immediate" execution
COMM_NTF = 0x0400  # request Result Register feedback (for presence diagnostics)
COMM_1_WIRE_RESET = 0x0042
COMM_BYTE_IO = 0x0052
COMM_BLOCK_IO = 0x0074

SPEED_NORMAL = 0x00

ST_IDLE = 0x20  # bit in the status byte: device is idle/ready again
ST_SIZE = 0x20  # 32-byte status response
FIFO_SIZE = 0x80  # 128-byte 1-Wire data buffer inside the DS2490

# Result Register flags (only valid in status bytes from index 16 onward, if COMM_NTF was set)
RR_NRS = 0x01  # Reset: no presence detected (no device answered!)
RR_SH = 0x02  # short circuit on the bus
RR_CMP = 0x10  # compare error (e.g. during Match ROM: byte echoed back differs)

# Standard 1-Wire ROM commands (chip-independent)
ROM_SKIP = 0xCC
ROM_MATCH = 0x55

# DS1996-specific memory commands
CMD_READ_MEMORY = 0xF0


def crc8_1wire(data: bytes) -> int:
    """Standard Dallas/Maxim 1-Wire CRC8 (polynomial x^8+x^5+x^4+1)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0x8C
            else:
                crc >>= 1
    return crc & 0xFF


def rom_id_to_bytes(rom_id_str: str) -> bytes:
    """
    Converts a ROM ID in the format 'ff-xxxxxxxxxxxx' (as shown in the Linux
    w1_master_slaves file, WITHOUT the CRC byte) into the full 8 bytes needed
    for a Match ROM command (including a freshly computed CRC8 as the last byte).
    """
    family_str, serial_str = rom_id_str.split("-")
    family_byte = bytes.fromhex(family_str)
    serial_bytes = bytes.fromhex(serial_str)
    partial = family_byte + serial_bytes
    if len(partial) != 7:
        raise ValueError(f"Unexpected ROM ID length: {rom_id_str}")
    crc = crc8_1wire(partial)
    return partial + bytes([crc])


class DS2490Error(RuntimeError):
    pass


class DS9490:
    """Minimal pyusb driver for the DS2490 chip inside the DS9490R adapter."""

    def __init__(self):
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if dev is None:
            raise DS2490Error(
                "No DS9490R found. Is the adapter plugged in? "
                "On Linux, try `sudo rmmod ds2490` first if the kernel driver "
                "is already holding the device."
            )
        self.dev = dev

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass  # e.g. not applicable on Windows

        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        # Alternate Setting 3: 1ms interrupt status polling, 64-byte bulk packets
        # (see the Linux kernel driver ds2490.c) -- speeds up polling.
        try:
            alt_intf = usb.util.find_descriptor(
                cfg, bInterfaceNumber=intf.bInterfaceNumber, bAlternateSetting=3
            )
            if alt_intf is not None:
                dev.set_interface_altsetting(
                    interface=intf.bInterfaceNumber, alternate_setting=3
                )
                intf = alt_intf
        except usb.core.USBError:
            pass

        self.ep_status = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_INTR,
        )
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
        )
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT
            and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK,
        )
        if not all([self.ep_status, self.ep_in, self.ep_out]):
            raise DS2490Error("Could not find the expected DS2490 endpoints.")

        self._reset_device()

    # -- Low-level DS2490 commands -----------------------------------

    def _send_control(self, cmd_type, value, index=0):
        self.dev.ctrl_transfer(
            VENDOR_REQUEST_TYPE, cmd_type, value, index, None, timeout=1000
        )

    def _reset_device(self):
        self._send_control(CONTROL_CMD, CTL_RESET_DEVICE, 0)
        self._send_control(MODE_CMD, MOD_PULSE_EN, PULSE_SPUE)

    def _recv_status(self) -> bytes:
        return bytes(
            self.dev.read(self.ep_status.bEndpointAddress, ST_SIZE, timeout=1000)
        )

    def _wait_idle(self, max_polls=100):
        for _ in range(max_polls):
            status = self._recv_status()
            if len(status) >= 9 and (status[8] & ST_IDLE):
                return status
            time.sleep(0.001)
        raise DS2490Error("Timeout: DS2490 never became idle.")

    def onewire_reset(self, check_presence=False):
        flags = COMM_1_WIRE_RESET | COMM_IM
        if check_presence:
            flags |= COMM_NTF
        self._send_control(COMM_CMD, flags, SPEED_NORMAL)
        status = self._wait_idle()
        if check_presence:
            extra = status[16:]  # Result Register bytes, if present
            if extra and (extra[0] & RR_NRS):
                print("    [!] RR_NRS set: NO presence pulse detected -- "
                      "no device responded to the reset!")
            elif extra and (extra[0] & RR_SH):
                print("    [!] RR_SH set: short circuit detected on the 1-Wire bus!")
            else:
                print(f"    [i] Status after reset: {status.hex()} (presence OK unless RR_NRS/RR_SH above)")

    def write_byte(self, byte: int) -> int:
        self._send_control(COMM_CMD, COMM_BYTE_IO | COMM_IM, byte)
        self._wait_idle()
        echoed = self.dev.read(self.ep_in.bEndpointAddress, 1, timeout=1000)
        return echoed[0]

    def read_byte(self) -> int:
        return self.write_byte(0xFF)

    def read_block(self, length: int) -> bytes:
        result = bytearray()
        remaining = length
        while remaining > 0:
            chunk_len = min(remaining, FIFO_SIZE)
            dummy = bytes([0xFF] * chunk_len)
            self.dev.write(self.ep_out.bEndpointAddress, dummy, timeout=1000)
            self._send_control(COMM_CMD, COMM_BLOCK_IO | COMM_IM, chunk_len)
            self._wait_idle()
            chunk = self.dev.read(self.ep_in.bEndpointAddress, chunk_len, timeout=2000)
            result.extend(chunk)
            remaining -= chunk_len
        return bytes(result)

    # -- Higher-level 1-Wire ROM functions ---------------------------------

    def skip_rom(self, debug=False):
        self.onewire_reset(check_presence=debug)
        self.write_byte(ROM_SKIP)

    def match_rom(self, rom_id_str: str, debug=False):
        self.onewire_reset(check_presence=debug)
        rom_bytes = rom_id_to_bytes(rom_id_str)
        if debug:
            print(f"    [i] Sending Match ROM (0x55) + ROM bytes: {rom_bytes.hex()}")
        self.write_byte(ROM_MATCH)
        for b in rom_bytes:
            echoed = self.write_byte(b)
            if debug and echoed != b:
                print(f"    [!] Echo mismatch sending 0x{b:02x}: DS2490 reported 0x{echoed:02x} back")


def read_ds1996_memory(
    ds: DS9490,
    rom_id: str,
    start_addr: int = 0,
    length: int = 8192,
    rom_mode: str = "match",
    debug: bool = False,
) -> bytes:
    """
    Reads 'length' bytes starting at 'start_addr' from a DS1996's NV-RAM.

    rom_mode="match": addresses the target device specifically via Match ROM
        (safe when multiple devices are on the bus -- e.g. a second DS1420
        identification chip, family 0x81).
    rom_mode="skip": simpler Skip ROM mode (debugging/comparison only, since
        it's ambiguous with multiple devices on the bus).
    """
    if rom_mode == "skip":
        ds.skip_rom(debug=debug)
    else:
        ds.match_rom(rom_id, debug=debug)

    ds.write_byte(CMD_READ_MEMORY)
    ds.write_byte(start_addr & 0xFF)  # TA1: address, low byte
    ds.write_byte((start_addr >> 8) & 0xFF)  # TA2: address, high byte
    return ds.read_block(length)


# DS1996 write commands (standard Dallas NV-RAM protocol)
CMD_WRITE_SCRATCHPAD = 0x0F
CMD_READ_SCRATCHPAD = 0xAA
CMD_COPY_SCRATCHPAD = 0x55


def write_scratchpad(ds: DS9490, rom_id: str, start_addr: int, data: bytes):
    """Writes up to 32 bytes into the scratchpad buffer (NOT directly into NV-RAM)."""
    if len(data) > 32:
        raise ValueError("The scratchpad holds a maximum of 32 bytes per write.")
    ds.match_rom(rom_id)
    ds.write_byte(CMD_WRITE_SCRATCHPAD)
    ds.write_byte(start_addr & 0xFF)
    ds.write_byte((start_addr >> 8) & 0xFF)
    for b in data:
        ds.write_byte(b)


def read_scratchpad(ds: DS9490, rom_id: str):
    """Reads back TA1/TA2/E-S byte + scratchpad content -- used for verification before commit."""
    ds.match_rom(rom_id)
    ds.write_byte(CMD_READ_SCRATCHPAD)
    ta1 = ds.read_byte()
    ta2 = ds.read_byte()
    es = ds.read_byte()
    ending_offset = es & 0x1F
    length = ending_offset + 1
    data = ds.read_block(length)
    return ta1, ta2, es, data


def copy_scratchpad(ds: DS9490, rom_id: str, ta1: int, ta2: int, es: int):
    """Permanently transfers the (verified) scratchpad content into NV-RAM."""
    ds.match_rom(rom_id)
    ds.write_byte(CMD_COPY_SCRATCHPAD)
    ds.write_byte(ta1)
    ds.write_byte(ta2)
    ds.write_byte(es)
    time.sleep(0.01)  # small safety delay for the internal commit


def write_memory_page(ds: DS9490, rom_id: str, start_addr: int, data: bytes):
    """
    Safely writes ONE page (up to 32 bytes): Write Scratchpad -> Read
    Scratchpad (byte-for-byte verification) -> only Copy Scratchpad on an
    exact match. Raises DS2490Error if verification fails (nothing is
    committed in that case -- the NV-RAM remains unchanged).
    """
    write_scratchpad(ds, rom_id, start_addr, data)
    ta1, ta2, es, readback = read_scratchpad(ds, rom_id)
    if (ta1, ta2) != (start_addr & 0xFF, (start_addr >> 8) & 0xFF):
        raise DS2490Error(
            f"Scratchpad target address mismatch: expected 0x{start_addr:04x}, "
            f"got TA1=0x{ta1:02x} TA2=0x{ta2:02x}"
        )
    if bytes(readback) != bytes(data):
        raise DS2490Error(
            "Scratchpad verification failed: data read back differs from "
            "data sent -- Copy Scratchpad will NOT be executed."
        )
    copy_scratchpad(ds, rom_id, ta1, ta2, es)


def erase_ds1996_memory(
    ds: DS9490,
    rom_id: str,
    start_addr: int = 16,
    length: int = 8176,
    fill_byte: int = 0xFF,
    progress_callback=None,
):
    """
    Overwrites the given memory range page by page (32 bytes) with fill_byte
    (default 0xFF, the observed 'blank' state). By default only the record
    area from byte 16 onward is erased -- the 16-byte header (firmware
    version + station number) is preserved.

    Aborts immediately on a verification failure (raises DS2490Error), so a
    partially erased/inconsistent state never goes unnoticed.
    """
    page_size = 32
    offset = start_addr
    end = start_addr + length
    blank_page = bytes([fill_byte] * page_size)

    while offset < end:
        chunk_len = min(page_size, end - offset)
        write_memory_page(ds, rom_id, offset, blank_page[:chunk_len])
        if progress_callback:
            progress_callback(offset + chunk_len, end)
        offset += chunk_len


def hexdump(data: bytes, width: int = 16) -> str:
    """
    Like a classic 'hexdump -C': identical, consecutive lines are collapsed
    into a single '*' line, so long runs of e.g. 0xFF don't produce hundreds
    of lines. The full content is preserved -- only repeats are hidden.
    """
    lines = []
    prev_chunk = None
    collapsing = False
    total = len(data)
    for offset in range(0, total, width):
        chunk = data[offset : offset + width]
        is_last = (offset + width) >= total
        if chunk == prev_chunk and not is_last:
            if not collapsing:
                lines.append("*")
                collapsing = True
            continue
        collapsing = False
        prev_chunk = chunk
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")
    lines.append(f"{total:08x}")  # final offset, like hexdump -C
    return "\n".join(lines)


def extract_strings(data: bytes, min_len: int = 4):
    """
    Finds contiguous readable ASCII sections (printable characters,
    length >= min_len) in the raw dump -- useful for spotting interesting
    content (station number, firmware tag, etc.) at a glance.
    Returns a list of (offset, text) tuples.
    """
    found = []
    current = bytearray()
    start = None
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if start is None:
                start = i
            current.append(b)
        else:
            if len(current) >= min_len:
                found.append((start, current.decode("ascii")))
            current = bytearray()
            start = None
    if len(current) >= min_len:
        found.append((start, current.decode("ascii")))
    return found


def bcd_to_int(byte_val: int):
    """Converts a BCD byte to a decimal integer, or None for invalid nibbles."""
    high, low = (byte_val >> 4) & 0x0F, byte_val & 0x0F
    if high > 9 or low > 9:
        return None
    return high * 10 + low


def read_raw_blocks(data: bytes, header_size: int = 16, block_size: int = 32):
    """
    Splits the dump into 32-byte blocks (after the 16-byte header). Each
    block is [name half(16)][data half(16)] -- BUT: a block's name half
    semantically belongs to the data half of the PREVIOUS block (confirmed
    empirically against real transaction exports). A trailing partial block
    (name only, no room left for data) is included with data_raw=None.
    """
    blocks = []
    offset = header_size
    while offset + block_size <= len(data):
        block = data[offset : offset + block_size]
        blocks.append({"offset": offset, "name_raw": block[0:16], "data_raw": block[16:32]})
        offset += block_size
    if offset + 16 <= len(data):
        blocks.append({"offset": offset, "name_raw": data[offset : offset + 16], "data_raw": None})
    return blocks


def parse_data_half(data_raw):
    """
    Parses the 16-byte data half of a block:
      [0:3]  Liters -- THREE BCD bytes concatenated, divided by 100
             (e.g. 03 00 04 -> "030004" -> 300.04 L)
      [3]    Day (BCD)
      [4]    Month (BCD)
      [5]    Year (BCD, 2-digit)
      [6]    Hour (BCD)
      [7]    Minute (BCD)
      [8]    Operator code
      [9:16] reserved/padding
    Returns None if blank (0xFF) or if the check fields are invalid.
    """
    if data_raw is None or len(data_raw) < 16 or all(b == 0xFF for b in data_raw):
        return None

    d0, d1, d2 = bcd_to_int(data_raw[0]), bcd_to_int(data_raw[1]), bcd_to_int(data_raw[2])
    day = bcd_to_int(data_raw[3])
    month = bcd_to_int(data_raw[4])
    year_2digit = bcd_to_int(data_raw[5])
    hour = bcd_to_int(data_raw[6])
    minute = bcd_to_int(data_raw[7])

    if None in (d0, d1, d2, day, month, year_2digit, hour, minute):
        return None
    if not (1 <= day <= 31 and 1 <= month <= 12 and hour <= 23 and minute <= 59):
        return None

    year = year_2digit + (2000 if year_2digit < 90 else 1900)
    liters = (d0 * 10000 + d1 * 100 + d2) / 100.0

    return {
        "liters": liters,
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "time": f"{hour:02d}:{minute:02d}",
        "operator": data_raw[8],
    }


def scan_records(data: bytes, header_size: int = 16, block_size: int = 32):
    """
    Assembles the complete transaction records: data half of block i +
    name half of block i+1 (see read_raw_blocks).
    """
    blocks = read_raw_blocks(data, header_size, block_size)
    records = []
    for i in range(len(blocks) - 1):
        parsed = parse_data_half(blocks[i]["data_raw"])
        if parsed is None:
            continue
        parsed["name"] = blocks[i + 1]["name_raw"].decode("ascii", errors="replace").strip()
        parsed["offset"] = blocks[i]["offset"]
        records.append(parsed)
    return records


def print_records_table(records):
    if not records:
        print("[i] No plausible records found (maybe adjust header/block size).")
        return
    header = f"{'Offset':>8} | {'Name':<16} | {'Date':<10} | {'Time':<5} | {'Liters':>9} | {'Op':>3}"
    print(header)
    print("-" * len(header))
    for r in records:
        print(
            f"0x{r['offset']:06x} | {r['name']:<16} | {r['date']:<10} | "
            f"{r['time']:<5} | {r['liters']:>9.2f} | {r['operator']:>3}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Direct USB read of a DS1996 via the DS9490R (pyusb, no kernel/TMEX driver)"
    )
    parser.add_argument("rom_id", help="ROM ID of the key, e.g. 0c-0000000000ab")
    parser.add_argument(
        "--length", type=int, default=8192, help="Number of bytes to read (default: 8192 = 64 kbit)"
    )
    parser.add_argument(
        "--start", type=lambda x: int(x, 0), default=0, help="Start address (default: 0)"
    )
    parser.add_argument(
        "--rom-mode", choices=["match", "skip"], default="match",
        help="'match' (default, recommended) or 'skip' (debugging only, with multiple devices on the bus)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print presence/echo diagnostics during the ROM handshake"
    )
    args = parser.parse_args()

    if not args.rom_id.lower().startswith("0c-"):
        print("[!] Warning: ROM ID does not start with '0c-' (DS1996 family). Continuing anyway.")

    print("[+] Connecting to the DS9490R...")
    ds = DS9490()
    print(f"[+] Reading {args.length} bytes from address 0x{args.start:04x} of {args.rom_id} "
          f"(rom-mode={args.rom_mode})...")
    data = read_ds1996_memory(
        ds, args.rom_id, args.start, args.length, rom_mode=args.rom_mode, debug=args.debug
    )

    print()
    print(hexdump(data))

    strings = extract_strings(data)
    if strings:
        print(f"\n[i] {len(strings)} readable text section(s) found:")
        for offset, text in strings:
            print(f"    0x{offset:04x}: {text!r}")
    else:
        print("\n[i] No readable ASCII sections found.")

    print("\n=== Detected transaction records ===")
    records = scan_records(data)
    print_records_table(records)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"dump_{args.rom_id.replace('/', '_')}_{timestamp}.bin"
    with open(out_name, "wb") as f:
        f.write(data)
    print(f"\n[SAVE] Saved as: {out_name}")


if __name__ == "__main__":
    main()
