"""
Tests for src/ds9490_direct.py without hardware.

A small simulator emulates the DS2490 USB protocol and a 1-Wire bus at bit
level (wired-AND), with a DS1996 key and the DS1420 ID chip of the adapter.

Run:  make test   (or: uv run python -m unittest discover -s tests -v)
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import usb.core  # noqa: E402

import ds9490_direct as d  # noqa: E402


def make_rom(family, serial_msb_first_hex):
    """Builds wire-order ROM bytes from sysfs-style parts."""
    partial = bytes([family]) + bytes.fromhex(serial_msb_first_hex)[::-1]
    return partial + bytes([d.crc8_1wire(partial)])


REAL_DS9490 = d.DS9490
KEY_ROM = make_rom(0x0C, "0000001db780")
ID_CHIP_ROM = make_rom(0x81, "00000a3c41f2")


# --------------------------------------------------------------------------
# 1-Wire bus simulation
# --------------------------------------------------------------------------


class SimSlave:
    def __init__(self, rom, memory=None):
        self.rom = rom
        self.memory = memory
        self.reset()

    def reset(self):
        self.state, self.bits, self.count = "rom", 0, 0

    def _rom_bit(self, k):
        return (self.rom[k // 8] >> (k % 8)) & 1

    def drive(self):
        """Level this slave puts on the bus in the next slot (1 = released)."""
        if self.state == "search":
            bit = self._rom_bit(self.count // 3)
            return {0: bit, 1: bit ^ 1, 2: 1}[self.count % 3]
        if self.state == "readmem":
            byte = self.memory[self.ptr % len(self.memory)]
            return (byte >> self.count) & 1
        return 1

    def receive(self, level):
        if self.state == "search":
            k, sub = divmod(self.count, 3)
            if sub == 2 and level != self._rom_bit(k):
                self.state = "idle"
                return
            self.count += 1
            if self.count == 64 * 3:
                self.state, self.bits, self.count = "func", 0, 0
            return
        if self.state == "readmem":
            self.count += 1
            if self.count == 8:
                self.count, self.ptr = 0, self.ptr + 1
            return
        if self.state in ("rom", "func", "match", "addr"):
            self.bits |= level << self.count
            self.count += 1
            done = {"rom": 8, "func": 8, "match": 64, "addr": 16}[self.state]
            if self.count < done:
                return
            value, self.bits, self.count = self.bits, 0, 0
            if self.state == "rom":
                self.state = {0xCC: "func", 0x55: "match", 0xF0: "search"}.get(value, "idle")
            elif self.state == "match":
                self.state = "func" if value.to_bytes(8, "little") == self.rom else "idle"
            elif self.state == "func":
                self.state = "addr" if (value == 0xF0 and self.memory) else "idle"
            elif self.state == "addr":
                self.state, self.ptr = "readmem", value


class SimBus:
    def __init__(self, slaves):
        self.slaves = slaves

    def reset(self):
        for s in self.slaves:
            s.reset()
        return bool(self.slaves)

    def bit(self, out):
        level = out
        for s in self.slaves:
            level &= s.drive()
        for s in self.slaves:
            s.receive(level)
        return level

    def byte(self, out):
        return sum(self.bit((out >> i) & 1) << i for i in range(8))


class SimDS2490:
    """Emulates the DS2490 vendor commands used by ds9490_direct."""

    STATUS_EP, IN_EP, OUT_EP = 0x81, 0x83, 0x02

    def __init__(self, bus):
        self.bus = bus
        self.out_fifo, self.in_fifo, self.results = bytearray(), bytearray(), bytearray()

    def ctrl_transfer(self, request_type, request, value, index, data, timeout):
        if request == d.CONTROL_CMD:
            self.out_fifo.clear(), self.in_fifo.clear(), self.results.clear()
        elif request == d.COMM_CMD:
            base = value & 0x00F0
            if base == 0x40:  # 1-Wire reset
                if not self.bus.reset():
                    self.results.append(d.RR_NRS)
            elif base == 0x50:  # byte I/O
                self.in_fifo.append(self.bus.byte(index & 0xFF))
            elif base == 0x20:  # bit I/O
                self.in_fifo.append(self.bus.bit(1 if value & d.COMM_D else 0))
            elif base == 0x70:  # block I/O
                block, self.out_fifo = self.out_fifo[:index], self.out_fifo[index:]
                self.in_fifo.extend(self.bus.byte(b) for b in block)

    def read(self, endpoint, size, timeout):
        if endpoint == self.STATUS_EP:
            status = bytearray(16)
            status[8] = d.ST_IDLE
            status += self.results
            self.results.clear()
            return status
        if not self.in_fifo:
            raise usb.core.USBTimeoutError("timeout")
        chunk, self.in_fifo = self.in_fifo[:size], self.in_fifo[size:]
        return chunk

    def write(self, endpoint, data, timeout):
        self.out_fifo.extend(data)
        return len(data)


def sim_ds9490(slaves):
    """A DS9490 instance wired to the simulator (bypasses USB enumeration)."""
    ds = REAL_DS9490.__new__(REAL_DS9490)
    ds.dev = SimDS2490(SimBus(slaves))
    ds._interface, ds._claimed = 0, False
    ds.ep_status = SimpleNamespace(bEndpointAddress=SimDS2490.STATUS_EP, wMaxPacketSize=32)
    ds.ep_in = SimpleNamespace(bEndpointAddress=SimDS2490.IN_EP, wMaxPacketSize=64)
    ds.ep_out = SimpleNamespace(bEndpointAddress=SimDS2490.OUT_EP, wMaxPacketSize=64)
    ds._reset_device()
    return ds


MEMORY = bytes((i * 7 + 3) & 0xFF for i in range(8192))


def bus_with_key():
    return [SimSlave(ID_CHIP_ROM), SimSlave(KEY_ROM, MEMORY)]


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class RomIdTests(unittest.TestCase):
    def test_sysfs_id_matches_wire_order(self):
        self.assertEqual(d.rom_id_to_bytes("0c-0000001db780"), KEY_ROM)
        self.assertEqual(d.rom_bytes_to_id(KEY_ROM), "0c-0000001db780")

    def test_roundtrip(self):
        for rom in (KEY_ROM, ID_CHIP_ROM):
            self.assertEqual(d.rom_id_to_bytes(d.rom_bytes_to_id(rom)), rom)

    def test_bad_crc_rejected(self):
        with self.assertRaises(ValueError):
            d.rom_bytes_to_id(KEY_ROM[:7] + b"\x00")

    def test_invalid_id_rejected(self):
        for bad in ("0c0000001db780", "0c-1234", "zz-0000001db780"):
            with self.assertRaises(ValueError):
                d.rom_id_to_bytes(bad)


class SearchTests(unittest.TestCase):
    def test_finds_all_devices(self):
        roms = sim_ds9490(bus_with_key()).search_roms()
        self.assertCountEqual(roms, [KEY_ROM, ID_CHIP_ROM])

    def test_family_search_finds_key(self):
        self.assertEqual(sim_ds9490(bus_with_key()).search_roms(family=0x0C), [KEY_ROM])

    def test_family_search_without_key(self):
        self.assertEqual(sim_ds9490([SimSlave(ID_CHIP_ROM)]).search_roms(family=0x0C), [])

    def test_empty_bus(self):
        self.assertEqual(sim_ds9490([]).search_roms(), [])

    def test_many_devices(self):
        roms = [make_rom(0x0C, f"0000001db7{i:02x}") for i in range(6)]
        found = sim_ds9490([SimSlave(r) for r in roms]).search_roms()
        self.assertCountEqual(found, roms)

    def test_find_ds1996_rom_id_usb(self):
        with mock.patch.object(d, "DS9490", lambda: sim_ds9490(bus_with_key())):
            self.assertEqual(d.find_ds1996_rom_id_usb(), "0c-0000001db780")


class ReadTests(unittest.TestCase):
    def test_read_with_match_rom(self):
        data = d.read_ds1996_memory(sim_ds9490(bus_with_key()), "0c-0000001db780", 0, 300)
        self.assertEqual(data, MEMORY[:300])

    def test_read_with_skip_rom(self):
        data = d.read_ds1996_memory(
            sim_ds9490(bus_with_key()), "0c-0000001db780", 16, 200, rom_mode="skip"
        )
        self.assertEqual(data, MEMORY[16:216])

    def test_old_byte_order_never_matched(self):
        """Regression: the old conversion did not reverse the serial, so
        Match ROM selected nothing and reads returned 0xFF only."""
        ds = sim_ds9490(bus_with_key())
        ds.onewire_reset()
        ds.write_byte(d.ROM_MATCH)
        partial = bytes.fromhex("0c0000001db780")
        for b in partial + bytes([d.crc8_1wire(partial)]):
            ds.write_byte(b)
        ds.write_byte(d.CMD_READ_MEMORY), ds.write_byte(0), ds.write_byte(0)
        self.assertEqual(ds.read_block(32), b"\xff" * 32)

    def test_match_rom_without_device_raises(self):
        with self.assertRaises(d.DS2490Error):
            sim_ds9490([]).match_rom("0c-0000001db780")

    def test_usb_timeout_becomes_ds2490error(self):
        ds = sim_ds9490(bus_with_key())
        with self.assertRaises(d.DS2490Error):
            ds._read_data(1)  # nothing queued -> simulated USB timeout


class ScratchpadDS1996:
    """Byte-level DS1996 NV-RAM model (datasheet behavior): the scratchpad
    covers ONE 32-byte page; bytes written past its end are dropped and set
    the OF flag, Read Scratchpad returns data from the target offset to the
    end of the scratchpad followed by 0xFF, Copy Scratchpad commits T..E."""

    def __init__(self, memory):
        self.memory = bytearray(memory)
        self.pad = bytearray(32)
        self.ta, self.es = 0, 0
        self.out = []
        self.cmd = None

    def match_rom(self, rom_id):
        self.cmd, self.args, self.out = None, [], []

    def write_byte(self, b):
        if self.cmd is None:
            self.cmd = b
            if b == d.CMD_READ_SCRATCHPAD:
                start = self.ta & 0x1F
                self.out = [self.ta & 0xFF, self.ta >> 8, self.es] + list(self.pad[start:])
            return b
        self.args.append(b)
        if self.cmd == d.CMD_WRITE_SCRATCHPAD and len(self.args) > 2:
            if len(self.args) == 3:
                self.ta, self.es = self.args[0] | self.args[1] << 8, 0
            offset = (self.ta & 0x1F) + len(self.args) - 3
            if offset > 31:
                self.es |= 0x40
            else:
                self.pad[offset] = b
                self.es = (self.es & 0xE0) | offset
        elif self.cmd == d.CMD_COPY_SCRATCHPAD and len(self.args) == 3:
            page = self.ta & ~0x1F
            for i in range(self.ta & 0x1F, (self.es & 0x1F) + 1):
                self.memory[page + i] = self.pad[i]
        return b

    def read_byte(self):
        return self.out.pop(0) if self.out else 0xFF

    def read_block(self, n):
        return bytes(self.read_byte() for _ in range(n))


def key_with_records(count=5):
    """16-byte header + `count` records in the real layout: block i holds the
    name of record i-1 and the data (liters/date/time as BCD) of record i."""
    mem = bytearray(b"\xff" * 8192)
    mem[:16] = b"V1.0      100001"
    for i in range(count + 1):
        block = 16 + i * 32
        if i > 0:
            mem[block : block + 16] = f"AB.CD.{i:<10}".encode()
        if i < count:
            mem[block + 16 : block + 25] = bytes([0x03, 0x00, 0x04, 0x15, 0x03, 0x24, 0x10, i, 1])
    return bytes(mem)


class EraseTests(unittest.TestCase):
    def test_erase_blanks_whole_record_area(self):
        """Regression: the erase used to write 32 bytes from address 16, i.e.
        across every page boundary. Only the upper half of each page was
        erased, the data halves survived and were re-read with a blank
        (0xFF) name -> duplicate transactions with an unreadable plate."""
        memory = key_with_records()
        key = ScratchpadDS1996(memory)
        self.assertEqual(len(d.scan_records(memory)), 5)
        with mock.patch.object(d.time, "sleep"):
            d.erase_ds1996_memory(key, "0c-0000001db780")
        self.assertEqual(bytes(key.memory[:16]), memory[:16])
        self.assertEqual(bytes(key.memory[16:]), b"\xff" * (len(memory) - 16))
        self.assertEqual(d.scan_records(bytes(key.memory)), [])

    def test_write_across_page_boundary_rejected(self):
        with self.assertRaises(ValueError):
            d.write_memory_page(ScratchpadDS1996(MEMORY), "0c-0000001db780", 16, b"\xff" * 32)

    def test_half_erased_record_is_skipped(self):
        """A valid data half next to a blank name half is not a transaction."""
        data = bytearray(key_with_records())
        for page in range(32, len(data), 32):
            data[page + 16 : page + 32] = b"\xff" * 16
        self.assertEqual(len(d.scan_records(bytes(key_with_records()))), 5)
        self.assertEqual(d.scan_records(bytes(data)), [])


def bcd(value):
    return (value // 10) << 4 | (value % 10)


def full_key(write_index=0x0E):
    """A key with all 255 ring-buffer slots in use, laid out like the real
    one: data half of record i at 0x20+32*i, its name at 0x30+32*i."""
    mem = bytearray(8192)
    mem[:16] = b"\x00\x00MCG_2." + bytes([write_index, 0]) + b"100001"
    for i in range(255):
        data = 0x20 + 32 * i
        mem[data : data + 16] = (
            bytes(
                [0x01, bcd(i % 100), 0x50, bcd(i % 28 + 1), 0x09, 0x26, bcd(i % 24), bcd(i % 60), 4]
            )
            + b"\x00\x00\x00    "
        )
        mem[data + 16 : data + 32] = f"AB.CD.{i:<10}".encode()
    return bytes(mem)


def piusi_erased(memory):
    """What the original PIUSI software leaves behind (observed on a real
    key): names blanked except the last slot, write index reset to 0."""
    mem = bytearray(memory)
    for offset in d.record_name_offsets()[:-1]:
        mem[offset : offset + 16] = b"\xff" * 16
    mem[d.HEADER_WRITE_INDEX] = 0
    return bytes(mem)


class PiusiEraseTests(unittest.TestCase):
    def _erase(self, memory):
        key = ScratchpadDS1996(memory)
        with mock.patch.object(d.time, "sleep"):
            writes = d.erase_ds1996_records(key, "0c-0000001db780", current=memory)
        return bytes(key.memory), writes

    def test_layout_constants(self):
        offsets = d.record_name_offsets()
        self.assertEqual((offsets[0], offsets[-1], len(offsets)), (0x30, 0x1FF0, 255))
        self.assertTrue(all(o % 32 == 16 for o in offsets))  # upper half of a page

    def test_full_key_is_parsed(self):
        memory = full_key()
        self.assertEqual(len(d.scan_records(memory)), 255)
        self.assertFalse(d.is_key_erased(memory))

    def test_erase_matches_original_software(self):
        memory = full_key()
        erased, writes = self._erase(memory)
        self.assertEqual(writes, 255 + 1)  # 255 names + header write index
        # Identical to what PIUSI leaves, except the last name is blank too.
        expected = bytearray(piusi_erased(memory))
        expected[0x1FF0:0x2000] = b"\xff" * 16
        self.assertEqual(erased, bytes(expected))
        self.assertEqual(erased[d.HEADER_WRITE_INDEX], 0)
        self.assertEqual(erased[:8] + erased[9:16], memory[:8] + memory[9:16])
        self.assertTrue(d.is_key_erased(erased))
        self.assertEqual(d.scan_records(erased), [])

    def test_data_halves_untouched(self):
        memory = full_key()
        erased, _ = self._erase(memory)
        for i in range(255):
            data = 0x20 + 32 * i
            self.assertEqual(erased[data : data + 16], memory[data : data + 16])

    def test_key_erased_by_piusi_needs_only_last_name(self):
        memory = piusi_erased(full_key())
        self.assertEqual(len(d.scan_records(memory)), 1)  # the leftover record
        erased, writes = self._erase(memory)
        self.assertEqual(writes, 1)
        self.assertTrue(d.is_key_erased(erased))
        self.assertEqual(d.scan_records(erased), [])

    def test_erased_key_needs_no_writes(self):
        erased, _ = self._erase(full_key())
        _, writes = self._erase(erased)
        self.assertEqual(writes, 0)

    def test_new_record_after_erase_is_read(self):
        """The dispenser writes the next record into slot 0 (write index 0)."""
        erased = bytearray(self._erase(full_key())[0])
        erased[0x20:0x30] = bytes([0x00, 0x42, 0x17, 0x05, 0x10, 0x26, 0x08, 0x15, 3]) + bytes(7)
        erased[0x30:0x40] = b"AB.CD.999       "
        erased[d.HEADER_WRITE_INDEX] = 1
        records = d.scan_records(bytes(erased))
        self.assertEqual(
            [(r["name"], r["date"], r["liters"]) for r in records],
            [("AB.CD.999", "2026-10-05", 42.17)],
        )

    def test_interrupted_erase_can_be_repeated(self):
        memory = full_key()
        key = ScratchpadDS1996(memory)
        real_write = d.write_memory_page
        calls = {"n": 0}

        def flaky_write(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 100:
                raise d.DS2490Error("contact lost")
            return real_write(*args, **kwargs)

        with (
            mock.patch.object(d.time, "sleep"),
            mock.patch.object(d, "write_memory_page", flaky_write),
        ):
            with self.assertRaises(d.DS2490Error):
                d.erase_ds1996_records(key, "0c-0000001db780", current=memory)
        half = bytes(key.memory)
        self.assertEqual(half[d.HEADER_WRITE_INDEX], 0x0E)  # header is written last
        with mock.patch.object(d.time, "sleep"):
            d.erase_ds1996_records(key, "0c-0000001db780", current=half)
        self.assertTrue(d.is_key_erased(bytes(key.memory)))


class OpenCloseTests(unittest.TestCase):
    """Windows behavior of opening/closing, with a mocked pyusb device."""

    ENDPOINTS = [
        SimpleNamespace(bEndpointAddress=0x81, bmAttributes=0x03, wMaxPacketSize=32),
        SimpleNamespace(bEndpointAddress=0x83, bmAttributes=0x02, wMaxPacketSize=64),
        SimpleNamespace(bEndpointAddress=0x02, bmAttributes=0x02, wMaxPacketSize=64),
    ]

    def _find_descriptor(self, obj, custom_match=None, **kwargs):
        if custom_match is None:
            return None  # no alternate setting 3
        return next((e for e in self.ENDPOINTS if custom_match(e)), None)

    def _patches(self, dev):
        return [
            mock.patch.object(d.usb.core, "find", return_value=dev),
            mock.patch.object(d.usb.util, "find_descriptor", self._find_descriptor),
            mock.patch.object(d.usb.util, "claim_interface"),
            mock.patch.object(d.usb.util, "release_interface"),
            mock.patch.object(d.usb.util, "dispose_resources"),
            mock.patch.object(d.DS9490, "_reset_device"),
        ]

    def _winusb_device(self):
        dev = mock.MagicMock()
        dev.set_configuration.side_effect = NotImplementedError("not supported on WinUSB")
        cfg = mock.MagicMock()
        cfg.__getitem__.return_value = SimpleNamespace(bInterfaceNumber=0)
        dev.get_active_configuration.return_value = cfg
        return dev

    def test_winusb_open_skips_set_configuration_and_releases(self):
        dev = self._winusb_device()
        patches = self._patches(dev)
        for p in patches:
            p.start()
        try:
            with mock.patch.object(d, "IS_WINDOWS", True):
                with d.DS9490() as ds:
                    self.assertIs(ds.dev, dev)
                dev.set_configuration.assert_not_called()
                dev.is_kernel_driver_active.assert_not_called()
                cleared = {c.args[0] for c in dev.clear_halt.call_args_list}
                self.assertEqual(cleared, {0x81, 0x83, 0x02})  # data toggles resynced
                d.usb.util.release_interface.assert_called_once()
                d.usb.util.dispose_resources.assert_called_once()
                self.assertIsNone(ds.dev)
        finally:
            for p in patches:
                p.stop()

    def test_busy_device_gives_clear_error_and_releases(self):
        dev = self._winusb_device()
        patches = self._patches(dev)
        for p in patches:
            p.start()
        try:
            d.usb.util.claim_interface.side_effect = usb.core.USBError("Access denied")
            with self.assertRaises(d.DS2490Error) as ctx:
                d.DS9490()
            self.assertIn("busy", str(ctx.exception))
            d.usb.util.dispose_resources.assert_called_once()
        finally:
            for p in patches:
                p.stop()

    def test_no_backend_becomes_ds2490error(self):
        with mock.patch.object(d.usb.core, "find", side_effect=usb.core.NoBackendError("x")):
            with self.assertRaises(d.DS2490Error) as ctx:
                d.DS9490()
        self.assertIn("libusb", str(ctx.exception))

    def test_adapter_missing(self):
        with mock.patch.object(d.usb.core, "find", return_value=None):
            with self.assertRaises(d.DS2490Error):
                d.DS9490()


class BackendTests(unittest.TestCase):
    def test_linux_uses_default_lookup(self):
        with mock.patch.object(d, "IS_WINDOWS", False):
            self.assertIsNone(d._usb_backend())

    def test_windows_build_uses_dll_from_bundle(self):
        import tempfile

        with tempfile.TemporaryDirectory() as bundle:
            dll = os.path.join(bundle, "libusb-1.0.dll")
            open(dll, "wb").close()
            seen = {}

            def fake_get_backend(find_library):
                seen["path"] = find_library("usb-1.0")
                return "backend"

            with (
                mock.patch.object(d, "IS_WINDOWS", True),
                mock.patch.object(d.sys, "_MEIPASS", bundle, create=True),
                mock.patch("usb.backend.libusb1.get_backend", fake_get_backend),
            ):
                self.assertEqual(d._usb_backend(), "backend")
            self.assertEqual(seen["path"], dll)


class MainDetectionTests(unittest.TestCase):
    def test_falls_back_to_usb_without_sysfs(self):
        import main

        with (
            mock.patch.object(main.os.path, "isdir", return_value=False),
            mock.patch.object(
                main, "find_ds1996_rom_id_usb", return_value="0c-0000001db780"
            ) as usb_search,
        ):
            self.assertEqual(main.find_ds1996_rom_id(), "0c-0000001db780")
            usb_search.assert_called_once()

    def test_usb_errors_do_not_crash_detection(self):
        import main

        with (
            mock.patch.object(main.os.path, "isdir", return_value=False),
            mock.patch.object(
                main, "find_ds1996_rom_id_usb", side_effect=d.DS2490Error("No DS9490R found.")
            ),
        ):
            self.assertIsNone(main.find_ds1996_rom_id())


if __name__ == "__main__":
    unittest.main()
