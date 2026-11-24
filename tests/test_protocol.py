"""Frame decoding, using frames captured from a real heater where available."""

from __future__ import annotations

import pytest

from custom_components.ha_bougerv_heater.const import is_standard_heater_name
from custom_components.ha_bougerv_heater.faults import FAULTS, describe_fault
from custom_components.ha_bougerv_heater.protocol import (
    RunState,
    build_command,
    checksum,
    firmware_query,
    parse_firmware,
    parse_status,
)

# Captured from a BougeRV-DHG001, firmware V2.1.2, heater off and idle.
OFF_FRAME = bytes.fromhex("80 41 00 ff 00 00 3d 00 00 00 00 02 32 18 00 00 00 00 00 49")
FIRMWARE_FRAME = bytes.fromhex("40 0e 01 01 01 02 01 02 56")


def _status(**bytes_at: int) -> bytes:
    """The captured off frame with some bytes replaced and the checksum fixed."""
    frame = bytearray(OFF_FRAME)
    for name, value in bytes_at.items():
        frame[int(name.removeprefix("b"))] = value
    frame[-1] = checksum(bytes(frame[:-1]))
    return bytes(frame)


def test_captured_off_frame() -> None:
    status = parse_status(OFF_FRAME)
    assert status is not None
    assert status.run_state is RunState.OFF
    assert not any(
        (status.fan, status.fuel_pump, status.glow_plug, status.flame, status.intake, status.exhaust)
    )
    assert status.fault_code is None
    assert not status.fault_active
    assert status.efficiency == 0
    assert status.actual_output_kw == 0.0
    assert status.output_setting_kw == 5.0
    assert status.voltage == 0.0
    assert status.total_run_hours == 0
    assert status.schedule_enabled is True
    assert status.schedule_hours == ()
    assert status.unknown_fields["byte_6"] == 61


def test_captured_firmware_reply() -> None:
    assert parse_firmware(FIRMWARE_FRAME) == "V2.1.2"


@pytest.mark.parametrize(
    ("flags", "state"),
    [
        (0b0000_0000, RunState.OFF),
        (0b0010_0001, RunState.RUNNING),  # on, flame, no glow plug
        (0b0011_0001, RunState.STARTING),  # on, flame, glow plug still lit
        (0b0000_0001, RunState.STARTING),  # on, no flame yet
        (0b0000_0010, RunState.SHUTTING_DOWN),
        (0b0000_0011, RunState.STARTING),
    ],
)
def test_run_state(flags: int, state: RunState) -> None:
    status = parse_status(_status(b2=flags))
    assert status is not None
    assert status.run_state is state


def test_component_flags() -> None:
    status = parse_status(_status(b2=0b1111_1101))
    assert status is not None
    assert (status.fan, status.fuel_pump, status.glow_plug) == (True, True, True)
    assert (status.flame, status.intake, status.exhaust) == (True, True, True)


def test_running_values() -> None:
    status = parse_status(
        _status(b3=3, b4=120, b5=34, b9=0x2C, b10=0x01, b12=30, b14=62, b15=0x22, b17=0x80)
    )
    assert status is not None
    assert status.fault_code == 3
    assert status.efficiency == 99  # clamped
    assert status.actual_output_kw == 3.4
    assert status.output_setting_kw == 3.0
    assert status.voltage == 12.4
    assert status.total_run_hours == 300
    # The app labels bit i as (i + 1):00: bits 1, 5, 23 are 02:00, 06:00, 24:00.
    assert status.schedule_hours == (2, 6, 24)


def test_schedule_hours_use_the_app_labels() -> None:
    status = parse_status(_status(b15=0x01, b17=0x80))
    assert status is not None
    assert status.schedule_hours == (1, 24)  # first slot 01:00, last 24:00


def test_bad_checksum_is_rejected() -> None:
    corrupted = bytearray(OFF_FRAME)
    corrupted[5] = 7
    assert parse_status(bytes(corrupted)) is None
    assert parse_firmware(FIRMWARE_FRAME[:-1] + b"\x00") is None


def test_wrong_or_short_frames_are_rejected() -> None:
    assert parse_status(FIRMWARE_FRAME) is None
    assert parse_status(OFF_FRAME[:10]) is None
    assert parse_firmware(OFF_FRAME) is None


@pytest.mark.parametrize(
    ("command", "payload", "expected"),
    [
        (0x05, b"\x01", "00 05 01 06"),
        (0x05, b"\x00", "00 05 00 05"),
        (0x07, b"\x1e", "00 07 1e 25"),
        (0x0A, bytes((26, 11, 23, 19, 45, 30)), "00 0a 1a 0b 17 13 2d 1e a4"),
        (0x0B, bytes((0, 0x22, 0, 0, 0)), "00 0b 00 22 00 00 00 2d"),
    ],
)
def test_command_framing_matches_spec(command: int, payload: bytes, expected: str) -> None:
    assert build_command(command, payload).hex(" ") == expected


def test_firmware_query() -> None:
    assert firmware_query() == bytes.fromhex("00 0e 0e")


@pytest.mark.parametrize(
    ("name", "supported"),
    [
        ("BougeRV-DHG001", True),
        ("BougeRV-DHG004", True),
        ("LFB00000", True),
        ("BougeRV-DHG005", False),  # Pro
        ("BougeRV-DHG10", False),  # Pro
        ("BougeRV-EBB01", False),
        (None, False),
    ],
)
def test_standard_heater_names(name: str | None, supported: bool) -> None:
    assert is_standard_heater_name(name) is supported


def test_every_defined_fault_has_text() -> None:
    assert sorted(FAULTS) == [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 16, 17]
    for fault in FAULTS.values():
        assert fault.name and fault.causes and fault.recovery
    assert describe_fault(16).label == "C4"
    assert describe_fault(17).label == "C7/CONN"


def test_undefined_fault_is_still_a_fault() -> None:
    fault = describe_fault(13)
    assert (fault.label, fault.name) == ("E13", "Unknown fault")
