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
    pyusb, plus a libusb-1.0 library:
      Windows: shipped by the "libusb-package" Python package (a project
               dependency). The DS9490R must use the WinUSB driver -- install it
               once with Zadig (https://zadig.akeo.ie/). The Maxim/TMEX
               "1-Wire Drivers" are NOT usable by libusb.
      Linux:   system libusb-1.0; the ds2490 kernel driver is detached
               automatically. Needs root or a udev rule for the DS9490R
               (idVendor=04fa, idProduct=2490).

Usage:
    python ds9490_direct.py                      # find the key over USB, then read it
    python ds9490_direct.py 0c-0000000000ab
    python ds9490_direct.py 0c-0000000000ab --length 8192 --start 0
"""

import argparse
import datetime
import os
import sys
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
COMM_D = 0x0008  # bit value for COMM_BIT_IO
COMM_BIT_IO = 0x0020
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
RESULT_DEVICE_DETECT = 0xA5  # "new device on the bus" notice -- NOT an error code

USB_TIMEOUT_MS = 2000  # generous: first transfers on Windows can be slow
IDLE_TIMEOUT_S = 3.0  # max time to wait for the DS2490 to finish a command

# Standard 1-Wire ROM commands (chip-independent)
ROM_SKIP = 0xCC
ROM_MATCH = 0x55
ROM_SEARCH = 0xF0

DS1996_FAMILY = 0x0C

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
    Converts a ROM ID like '0c-0000001db780' into the 8 bytes sent on the wire
    for Match ROM: family, serial, CRC8.

    The ID string uses the Linux sysfs notation: family byte, then the 48-bit
    serial number printed MOST significant byte first. On the 1-Wire bus the
    serial is transmitted LEAST significant byte first, so it must be reversed.
    """
    try:
        family_str, serial_str = rom_id_str.strip().lower().split("-")
        family_byte = bytes.fromhex(family_str)
        serial_bytes = bytes.fromhex(serial_str)
    except ValueError as e:
        raise ValueError(f"Invalid ROM ID: {rom_id_str!r}") from e
    if len(family_byte) != 1 or len(serial_bytes) != 6:
        raise ValueError(f"Unexpected ROM ID length: {rom_id_str}")
    partial = family_byte + serial_bytes[::-1]
    return partial + bytes([crc8_1wire(partial)])


def rom_bytes_to_id(rom: bytes) -> str:
    """Inverse of rom_id_to_bytes(): 8 wire-order ROM bytes -> '0c-0000001db780'.
    Produces exactly the Linux sysfs notation, so IDs (and the record hashes
    built from them) are identical no matter how the key was detected."""
    if len(rom) != 8:
        raise ValueError(f"ROM must be 8 bytes, got {len(rom)}")
    if crc8_1wire(rom[:7]) != rom[7]:
        raise ValueError(f"ROM CRC mismatch: {bytes(rom).hex()}")
    return f"{rom[0]:02x}-{bytes(rom[1:7])[::-1].hex()}"


class DS2490Error(RuntimeError):
    pass


IS_WINDOWS = sys.platform.startswith("win")

DRIVER_HINT = (
    "On Windows the DS9490R needs the WinUSB driver (install it once with Zadig, "
    "https://zadig.akeo.ie/) and must not be in use by another program "
    "(e.g. PIUSI SelfService or a second FluidTrack instance)."
    if IS_WINDOWS
    else "On Linux, check the udev rule / permissions and that no other program "
    "is using the adapter."
)


def _libusb_dll_candidates():
    """Where libusb-1.0.dll can be on Windows, in order of preference."""
    candidates = []
    # PyInstaller build: the libusb-package hook copies the DLL into the
    # bundle root (_internal/), where libusb_package itself does not look.
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.append(os.path.join(bundle_dir, "libusb-1.0.dll"))
    # Source run: the DLL shipped inside the libusb-package wheel.
    try:
        import libusb_package

        path = libusb_package.get_library_path()
        if path:
            candidates.append(str(path))
    except Exception:
        pass
    return candidates


def _usb_backend():
    """pyusb cannot find libusb-1.0.dll on Windows by itself, so the DLL is
    located explicitly. Elsewhere pyusb's default lookup (system libusb) is used.
    Returns None to let pyusb try its own search (e.g. a DLL on PATH)."""
    if not IS_WINDOWS:
        return None
    import usb.backend.libusb1

    for path in _libusb_dll_candidates():
        if os.path.isfile(path):
            backend = usb.backend.libusb1.get_backend(find_library=lambda _name, p=path: p)
            if backend is not None:
                return backend
    return None


class DS9490:
    """Minimal pyusb driver for the DS2490 chip inside the DS9490R adapter.

    Use it as a context manager so the USB device is always released --
    WinUSB allows only ONE open handle, so a leaked handle makes every
    following open fail with "Access denied":

        with DS9490() as ds:
            ...
    """

    def __init__(self):
        self.dev = None
        self._interface = 0
        self._claimed = False

        try:
            dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=_usb_backend())
        except usb.core.NoBackendError as e:
            raise DS2490Error(
                "No libusb backend available. "
                + (
                    "Install the 'libusb-package' Python package (run 'uv sync')."
                    if IS_WINDOWS
                    else "Install libusb-1.0 (e.g. 'sudo apt install libusb-1.0-0')."
                )
            ) from e
        if dev is None:
            raise DS2490Error("No DS9490R found. Is the adapter plugged in? " + DRIVER_HINT)
        self.dev = dev

        try:
            self._open()
        except DS2490Error:
            self.close()
            raise
        except (usb.core.USBError, NotImplementedError, ValueError, KeyError) as e:
            self.close()
            raise DS2490Error(f"Could not open the DS9490R: {e}. {DRIVER_HINT}") from e

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _open(self):
        dev = self.dev

        if not IS_WINDOWS:
            try:
                if dev.is_kernel_driver_active(0):
                    dev.detach_kernel_driver(0)
            except (usb.core.USBError, NotImplementedError):
                pass

        # WinUSB does not support SET_CONFIGURATION (NotImplementedError), and the
        # Windows driver has configured the device already. So only configure
        # the device if it isn't configured yet.
        try:
            cfg = dev.get_active_configuration()
        except (usb.core.USBError, NotImplementedError):
            cfg = None
        if cfg is None:
            dev.set_configuration()
            cfg = dev.get_active_configuration()

        intf = cfg[(0, 0)]
        self._interface = intf.bInterfaceNumber

        try:
            usb.util.claim_interface(dev, self._interface)
            self._claimed = True
        except usb.core.USBError as e:
            raise DS2490Error(f"DS9490R is busy or not accessible ({e}). {DRIVER_HINT}") from e

        # Alternate Setting 3: 1ms interrupt status polling, 64-byte bulk packets
        # (see the Linux kernel driver ds2490.c) -- speeds up polling. Optional:
        # setting 0 works too, just slower.
        alt_intf = usb.util.find_descriptor(
            cfg, bInterfaceNumber=self._interface, bAlternateSetting=3
        )
        if alt_intf is not None:
            try:
                dev.set_interface_altsetting(interface=self._interface, alternate_setting=3)
                intf = alt_intf
            except (usb.core.USBError, NotImplementedError):
                pass

        def endpoint(direction, ep_type):
            return usb.util.find_descriptor(
                intf,
                custom_match=lambda e: (
                    usb.util.endpoint_direction(e.bEndpointAddress) == direction
                    and usb.util.endpoint_type(e.bmAttributes) == ep_type
                ),
            )

        self.ep_status = endpoint(usb.util.ENDPOINT_IN, usb.util.ENDPOINT_TYPE_INTR)
        self.ep_in = endpoint(usb.util.ENDPOINT_IN, usb.util.ENDPOINT_TYPE_BULK)
        self.ep_out = endpoint(usb.util.ENDPOINT_OUT, usb.util.ENDPOINT_TYPE_BULK)
        if not all([self.ep_status, self.ep_in, self.ep_out]):
            raise DS2490Error("Could not find the expected DS2490 endpoints.")

        # Resynchronize the USB data toggles of all endpoints. Without this,
        # host and DS2490 can disagree after a previous session, and the host
        # silently drops the next reply packet as a "duplicate" -> bulk read
        # timeout on every 2nd open (the old code got this as a side effect of
        # set_configuration(), which WinUSB does not support).
        for ep in (self.ep_status, self.ep_in, self.ep_out):
            try:
                dev.clear_halt(ep.bEndpointAddress)
            except (usb.core.USBError, NotImplementedError):
                pass

        self._reset_device()

    def close(self):
        """Releases the USB device (safe to call more than once)."""
        dev, self.dev = self.dev, None
        if dev is None:
            return
        if self._claimed:
            try:
                usb.util.release_interface(dev, self._interface)
            except Exception:
                pass
            self._claimed = False
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass

    # -- Low-level USB transfers (all USB errors become DS2490Error) -------

    def _usb(self, what, func, *args, **kwargs):
        if self.dev is None:
            raise DS2490Error("DS9490R is closed.")
        try:
            return func(*args, **kwargs)
        except usb.core.USBTimeoutError as e:
            raise DS2490Error(f"USB timeout during {what}.") from e
        except (usb.core.USBError, NotImplementedError) as e:
            raise DS2490Error(f"USB error during {what}: {e}") from e

    def _send_control(self, cmd_type, value, index=0):
        self._usb(
            "control transfer",
            self.dev.ctrl_transfer,
            VENDOR_REQUEST_TYPE,
            cmd_type,
            value,
            index,
            None,
            timeout=USB_TIMEOUT_MS,
        )

    def _reset_device(self):
        self._send_control(CONTROL_CMD, CTL_RESET_DEVICE, 0)
        self._send_control(MODE_CMD, MOD_PULSE_EN, PULSE_SPUE)

    def _recv_status(self) -> bytes:
        size = max(ST_SIZE, self.ep_status.wMaxPacketSize)
        return bytes(
            self._usb(
                "status read",
                self.dev.read,
                self.ep_status.bEndpointAddress,
                size,
                timeout=USB_TIMEOUT_MS,
            )
        )

    def _wait_idle(self) -> bytes:
        """Polls the status endpoint until the DS2490 is idle. Returns all
        Result Register bytes (status bytes 16+) seen while waiting -- a result
        can arrive in an earlier status packet than the idle flag."""
        deadline = time.monotonic() + IDLE_TIMEOUT_S
        results = bytearray()
        while True:
            status = self._recv_status()
            if len(status) > 16:
                results.extend(status[16:])
            if len(status) >= 9 and (status[8] & ST_IDLE):
                return bytes(results)
            if time.monotonic() > deadline:
                raise DS2490Error("Timeout: DS2490 never became idle.")
            time.sleep(0.001)

    def _read_data(self, length: int) -> bytes:
        """Reads exactly `length` bytes from the bulk IN pipe. The read buffer
        is rounded up to whole USB packets -- WinUSB/libusb report an overflow
        error if the buffer is smaller than the packet the device sends."""
        packet = self.ep_in.wMaxPacketSize or 64
        deadline = time.monotonic() + IDLE_TIMEOUT_S
        result = bytearray()
        while len(result) < length:
            missing = length - len(result)
            size = -(-missing // packet) * packet
            chunk = self._usb(
                "bulk read",
                self.dev.read,
                self.ep_in.bEndpointAddress,
                size,
                timeout=USB_TIMEOUT_MS,
            )
            result.extend(chunk)
            if len(result) < length and time.monotonic() > deadline:
                raise DS2490Error(f"Timeout: got only {len(result)} of {length} bytes.")
        if len(result) > length:
            raise DS2490Error(
                f"Unexpected data from DS2490: expected {length} bytes, got {len(result)}."
            )
        return bytes(result)

    def _write_data(self, data: bytes):
        written = self._usb(
            "bulk write", self.dev.write, self.ep_out.bEndpointAddress, data, timeout=USB_TIMEOUT_MS
        )
        if written != len(data):
            raise DS2490Error(f"Bulk write incomplete: {written} of {len(data)} bytes.")

    # -- 1-Wire primitives ---------------------------------------------------

    def onewire_reset(self, check_presence=False) -> bool:
        """Sends a 1-Wire reset. Returns True if at least one device answered
        with a presence pulse. Raises DS2490Error on a short circuit."""
        flags = COMM_1_WIRE_RESET | COMM_IM
        if check_presence:
            flags |= COMM_NTF
        self._send_control(COMM_CMD, flags, SPEED_NORMAL)
        results = self._wait_idle()

        errors = [b for b in results if b != RESULT_DEVICE_DETECT]
        if any(b & RR_SH for b in errors):
            raise DS2490Error("Short circuit detected on the 1-Wire bus.")
        presence = not any(b & RR_NRS for b in errors)

        if check_presence:
            if presence:
                print(f"    [i] Presence OK (result bytes: {results.hex() or 'none'})")
            else:
                print("    [!] RR_NRS set: NO presence pulse -- no device responded to the reset!")
        return presence

    def write_byte(self, byte: int) -> int:
        self._send_control(COMM_CMD, COMM_BYTE_IO | COMM_IM, byte)
        self._wait_idle()
        return self._read_data(1)[0]

    def read_byte(self) -> int:
        return self.write_byte(0xFF)

    def touch_bit(self, bit: int) -> int:
        """Writes one bit (1 = read slot) and returns the bit read back."""
        self._send_control(COMM_CMD, COMM_BIT_IO | COMM_IM | (COMM_D if bit else 0), 0)
        self._wait_idle()
        return self._read_data(1)[0] & 0x01

    def read_block(self, length: int) -> bytes:
        result = bytearray()
        remaining = length
        while remaining > 0:
            chunk_len = min(remaining, FIFO_SIZE)
            self._write_data(bytes([0xFF] * chunk_len))
            self._send_control(COMM_CMD, COMM_BLOCK_IO | COMM_IM, chunk_len)
            self._wait_idle()
            result.extend(self._read_data(chunk_len))
            remaining -= chunk_len
        return bytes(result)

    # -- Higher-level 1-Wire ROM functions ---------------------------------

    def search_roms(self, family=None, max_devices=16):
        """
        Standard 1-Wire Search ROM (Maxim application note 187), done in
        software via single-bit I/O -- works on every OS, no kernel driver
        needed. Returns a list of 8-byte ROMs in wire order.

        family: if given, only searches for a device of this family code
            ("target setup" from AN187) and returns at most one ROM.
        """
        rom = bytearray(8)
        last_discrepancy = 0
        if family is not None:
            rom[0] = family
            last_discrepancy = 64

        found = []
        for _ in range(max_devices):
            if not self.onewire_reset():
                self._reset_device()  # DS2490 halts after an error result
                return found
            self.write_byte(ROM_SEARCH)

            last_zero = 0
            for bit_number in range(1, 65):
                index, mask = (bit_number - 1) // 8, 1 << ((bit_number - 1) % 8)
                id_bit = self.touch_bit(1)
                cmp_bit = self.touch_bit(1)
                if id_bit and cmp_bit:
                    return found  # no device answered this search pass
                if id_bit != cmp_bit:
                    direction = id_bit
                else:
                    if bit_number < last_discrepancy:
                        direction = 1 if rom[index] & mask else 0
                    else:
                        direction = 1 if bit_number == last_discrepancy else 0
                    if direction == 0:
                        last_zero = bit_number
                if direction:
                    rom[index] |= mask
                else:
                    rom[index] &= ~mask & 0xFF
                self.touch_bit(direction)

            if crc8_1wire(bytes(rom[:7])) != rom[7]:
                raise DS2490Error(
                    f"CRC error during ROM search ({bytes(rom).hex()}) -- bad contact?"
                )
            if family is not None:
                return [bytes(rom)] if rom[0] == family else []
            found.append(bytes(rom))
            last_discrepancy = last_zero
            if last_discrepancy == 0:
                break
        return found

    def skip_rom(self, debug=False):
        self.onewire_reset(check_presence=debug)
        self.write_byte(ROM_SKIP)

    def match_rom(self, rom_id_str: str, debug=False):
        rom_bytes = rom_id_to_bytes(rom_id_str)
        if not self.onewire_reset(check_presence=debug):
            self._reset_device()
            raise DS2490Error("No device answered the 1-Wire reset (key removed?).")
        if debug:
            print(f"    [i] Sending Match ROM (0x55) + ROM bytes: {rom_bytes.hex()}")
        self.write_byte(ROM_MATCH)
        for b in rom_bytes:
            echoed = self.write_byte(b)
            if echoed != b:
                raise DS2490Error(
                    f"Match ROM echo mismatch (sent 0x{b:02x}, got 0x{echoed:02x}) -- bad contact?"
                )


def find_ds1996_rom_id_usb():
    """Detects a DS1996 key directly over USB (no kernel driver needed).
    Returns its ID in sysfs notation ('0c-...') or None if no key is present."""
    with DS9490() as ds:
        roms = ds.search_roms(family=DS1996_FAMILY)
    return rom_bytes_to_id(roms[0]) if roms else None


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

PAGE_SIZE = 32  # DS1996 NV-RAM page = scratchpad size
ES_FLAGS_MASK = 0x60  # E/S byte: OF (overflow, bit 6) | PF (partial byte, bit 5)


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
    """Reads back TA1/TA2/E-S byte + scratchpad content -- used for verification before commit.

    The device returns the scratchpad starting at the target offset (TA1 & 0x1F),
    not at offset 0, so only the bytes from there up to the ending offset are read."""
    ds.match_rom(rom_id)
    ds.write_byte(CMD_READ_SCRATCHPAD)
    ta1 = ds.read_byte()
    ta2 = ds.read_byte()
    es = ds.read_byte()
    start_offset = ta1 & 0x1F
    ending_offset = es & 0x1F
    length = max(ending_offset - start_offset + 1, 0)
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

    The data must not cross a 32-byte page boundary: the scratchpad only
    covers one page, bytes beyond its end are dropped (OF flag) and the
    rest of the range would silently stay unwritten.
    """
    if not data:
        return
    if (start_addr % PAGE_SIZE) + len(data) > PAGE_SIZE:
        raise ValueError(
            f"Write of {len(data)} bytes at 0x{start_addr:04x} crosses a "
            f"{PAGE_SIZE}-byte page boundary."
        )
    write_scratchpad(ds, rom_id, start_addr, data)
    ta1, ta2, es, readback = read_scratchpad(ds, rom_id)
    if (ta1, ta2) != (start_addr & 0xFF, (start_addr >> 8) & 0xFF):
        raise DS2490Error(
            f"Scratchpad target address mismatch: expected 0x{start_addr:04x}, "
            f"got TA1=0x{ta1:02x} TA2=0x{ta2:02x}"
        )
    expected_end = (start_addr + len(data) - 1) % PAGE_SIZE
    if es & ES_FLAGS_MASK or (es & 0x1F) != expected_end:
        raise DS2490Error(
            f"Scratchpad E/S byte unexpected: 0x{es:02x} (expected ending "
            f"offset 0x{expected_end:02x}, no OF/PF flags)"
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
    Low-level tool, NOT used by the app (see erase_ds1996_records for the
    erase that matches the original PIUSI software).

    Overwrites the given memory range page by page (aligned to the 32-byte
    pages) with fill_byte (default 0xFF, the observed 'blank' state). By default only the record
    area from byte 16 onward is erased -- the 16-byte header (firmware
    version + station number) is preserved.

    Aborts immediately on a verification failure (raises DS2490Error), so a
    partially erased/inconsistent state never goes unnoticed.
    """
    offset = start_addr
    end = start_addr + length
    blank_page = bytes([fill_byte] * PAGE_SIZE)

    while offset < end:
        # Never cross a page boundary: the default start (16) is mid-page, so
        # the first chunk is only 16 bytes long, all following ones are aligned.
        chunk_len = min(PAGE_SIZE - offset % PAGE_SIZE, end - offset)
        write_memory_page(ds, rom_id, offset, blank_page[:chunk_len])
        if progress_callback:
            progress_callback(offset + chunk_len, end)
        offset += chunk_len


# --- Key layout (reverse-engineered, see read_raw_blocks) -------------------
KEY_SIZE = 8192
HEADER_SIZE = 16
RECORD_SIZE = 32
RECORD_SLOTS = 255  # ring buffer: (8192 - 16) // 32
NAME_SIZE = 16
# Header byte 8: ring-buffer slot the dispenser writes next (0..254). On a key
# holding 14 records in slots 0-13 it read 0x0E; erasing resets it to 0.
HEADER_WRITE_INDEX = 8


def record_name_offsets():
    """Addresses of the name halves: record i keeps its name in the upper half
    of the NEXT block, i.e. at 0x30 + 32*i (always the upper half of a page)."""
    return [HEADER_SIZE + RECORD_SIZE * (i + 1) for i in range(RECORD_SLOTS)]


def is_key_erased(data: bytes) -> bool:
    """True if the key is in the erased state: write index 0 and every name
    half blank (0xFF). The data halves may still hold old values -- without a
    name they no longer count as records (see scan_records)."""
    if len(data) < KEY_SIZE or data[HEADER_WRITE_INDEX] != 0:
        return False
    return all(
        all(b == 0xFF for b in data[offset : offset + NAME_SIZE])
        for offset in record_name_offsets()
    )


def erase_ds1996_records(ds: DS9490, rom_id: str, current: bytes = None, progress_callback=None):
    """
    Erases the key the way the PIUSI SelfService software does (determined
    from a key erased by the original software):

      - every record's name half (16 bytes) is overwritten with 0xFF,
      - the write index in header byte 8 is reset to 0, so the dispenser
        starts writing at slot 0 again,
      - the data halves (liters/date/time) and the rest of the header stay
        untouched.

    One deliberate difference: the original software leaves the name of the
    last slot (0x1FF0) in place, so one old record stays readable. It is
    cleared here as well, so an erased key reads as empty.

    Already blank name halves are skipped (fewer writes, fast retries). The
    header is written LAST, so an interrupted erase can simply be repeated.
    Every page write is verified via the scratchpad before it is committed.
    Returns the number of page writes performed.
    """
    if current is None:
        current = read_ds1996_memory(ds, rom_id, 0, KEY_SIZE)
    if len(current) < KEY_SIZE:
        raise ValueError(f"Need the full {KEY_SIZE}-byte memory image, got {len(current)}.")

    blank_name = bytes([0xFF] * NAME_SIZE)
    todo = [o for o in record_name_offsets() if current[o : o + NAME_SIZE] != blank_name]
    reset_index = current[HEADER_WRITE_INDEX] != 0
    total = len(todo) + (1 if reset_index else 0)

    for done, offset in enumerate(todo, 1):
        write_memory_page(ds, rom_id, offset, blank_name)
        if progress_callback:
            progress_callback(done, total)
    if reset_index:
        write_memory_page(ds, rom_id, HEADER_WRITE_INDEX, b"\x00")
        if progress_callback:
            progress_callback(total, total)
    return total


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
        name_raw = blocks[i + 1]["name_raw"]
        if all(b == 0xFF for b in name_raw):
            # Blank name half next to a valid data half: leftover of an
            # incomplete erase, not a real transaction.
            continue
        parsed["name"] = name_raw.decode("ascii", errors="replace").strip()
        parsed["offset"] = blocks[i]["offset"]
        records.append(parsed)
    return records


def print_records_table(records):
    if not records:
        print("[i] No plausible records found (maybe adjust header/block size).")
        return
    header = (
        f"{'Offset':>8} | {'Name':<16} | {'Date':<10} | {'Time':<5} | {'Liters':>9} | {'Op':>3}"
    )
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
    parser.add_argument(
        "rom_id",
        nargs="?",
        help="ROM ID of the key, e.g. 0c-0000000000ab (default: search the bus over USB)",
    )
    parser.add_argument(
        "--length", type=int, default=8192, help="Number of bytes to read (default: 8192 = 64 kbit)"
    )
    parser.add_argument(
        "--start", type=lambda x: int(x, 0), default=0, help="Start address (default: 0)"
    )
    parser.add_argument(
        "--rom-mode",
        choices=["match", "skip"],
        default="match",
        help="'match' (default, recommended) or 'skip' "
        "(debugging only, with multiple devices on the bus)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print presence/echo diagnostics during the ROM handshake",
    )
    args = parser.parse_args()

    if args.rom_id is None:
        print("[+] Searching the 1-Wire bus for a DS1996 key...")
        args.rom_id = find_ds1996_rom_id_usb()
        if args.rom_id is None:
            raise SystemExit("[-] No DS1996 key found on the bus.")
        print(f"[+] Found key: {args.rom_id}")
    elif not args.rom_id.lower().startswith("0c-"):
        print("[!] Warning: ROM ID does not start with '0c-' (DS1996 family). Continuing anyway.")

    print("[+] Connecting to the DS9490R...")
    with DS9490() as ds:
        print(
            f"[+] Reading {args.length} bytes from address 0x{args.start:04x} of {args.rom_id} "
            f"(rom-mode={args.rom_mode})..."
        )
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
