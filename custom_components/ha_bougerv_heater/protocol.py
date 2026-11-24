"""BougeRV standard diesel heater BLE protocol.

Pure functions with no Home Assistant imports, so they can be tested on their
own. See docs/TECH_SPEC.md for the frame layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

FRAME_STATUS = 0x41
FRAME_FIRMWARE = 0x0E

CMD_POWER = 0x05
CMD_HEAT_OUTPUT = 0x07
CMD_FIRMWARE_QUERY = 0x0E

# Heat output in tenths of a kW: 1.4-5.0 kW.
OUTPUT_MIN_RAW = 14
OUTPUT_MAX_RAW = 50

# Status frames carry 18 data bytes plus at least a trailing checksum.
STATUS_MIN_LENGTH = 19
FIRMWARE_MIN_LENGTH = 9

NO_FAULT = 0xFF


class RunState(StrEnum):
    """Run state decoded from status byte 2, bits 0-1, plus the flame flags."""

    OFF = "off"
    STARTING = "starting"
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"


def checksum(data: bytes) -> int:
    """Return the low 8 bits of the sum of every byte."""
    return sum(data) & 0xFF


def checksum_ok(frame: bytes) -> bool:
    """Return True when the last byte is the checksum of the bytes before it."""
    return len(frame) >= 3 and checksum(frame[:-1]) == frame[-1]


def build_command(command: int, payload: bytes = b"") -> bytes:
    """Build ``00 <command> [payload] <checksum>``."""
    body = bytes((0x00, command)) + payload
    return body + bytes((checksum(body),))


def firmware_query() -> bytes:
    """Return the firmware version query, ``00 0E 0E``."""
    return build_command(CMD_FIRMWARE_QUERY)


def power_command(on: bool) -> bytes:
    """Return ``00 05 01 06`` (on) or ``00 05 00 05`` (off)."""
    return build_command(CMD_POWER, b"\x01" if on else b"\x00")


def heat_output_command(raw: int) -> bytes:
    """Return the heat output command for ``raw`` tenths of a kW (14-50)."""
    if not OUTPUT_MIN_RAW <= raw <= OUTPUT_MAX_RAW:
        raise ValueError(f"heat output {raw} outside {OUTPUT_MIN_RAW}-{OUTPUT_MAX_RAW}")
    return build_command(CMD_HEAT_OUTPUT, bytes((raw,)))


def kw_to_raw(kw: float) -> int:
    """Convert a kW value from the UI to tenths of a kW."""
    return int(round(kw * 10))


def _s8(value: int) -> int:
    return value - 256 if value > 127 else value


@dataclass(frozen=True, slots=True)
class HeaterStatus:
    """One decoded status frame."""

    raw: bytes
    run_state: RunState
    fan: bool
    fuel_pump: bool
    glow_plug: bool
    flame: bool
    intake: bool
    exhaust: bool
    fault_code: int | None
    efficiency: int
    actual_output_kw: float
    output_setting_kw: float
    voltage: float
    total_run_hours: int
    schedule_enabled: bool
    schedule_hours: tuple[int, ...]

    @property
    def fault_active(self) -> bool:
        return self.fault_code is not None

    @property
    def on_flag(self) -> bool:
        """State bits ``01``: commanded on, whether or not the flame is lit yet."""
        return self.raw[2] & 0b11 == 0b01

    @property
    def is_on(self) -> bool:
        """Starting or running (not off, not in cool-down)."""
        return self.run_state in (RunState.STARTING, RunState.RUNNING)

    @property
    def unknown_fields(self) -> dict[str, int]:
        """Bytes whose meaning is not yet established, for diagnostics."""
        raw = self.raw
        return {
            "byte_0": raw[0],
            "byte_6": raw[6],
            "byte_7": raw[7],
            "byte_8": raw[8],
            "byte_11_bit_0": raw[11] & 0x01,
            "byte_13": raw[13],
            "byte_18": raw[18],
        }


def _run_state(flags: int) -> RunState:
    bits = flags & 0b11
    if bits == 0b00:
        return RunState.OFF
    if bits == 0b10:
        return RunState.SHUTTING_DOWN
    if bits == 0b11:
        return RunState.STARTING
    flame = bool(flags & (1 << 5))
    glow_plug = bool(flags & (1 << 4))
    return RunState.RUNNING if flame and not glow_plug else RunState.STARTING


def parse_status(frame: bytes) -> HeaterStatus | None:
    """Decode a status notification, or return None if it isn't a valid one."""
    if len(frame) < STATUS_MIN_LENGTH or frame[1] != FRAME_STATUS:
        return None
    if not checksum_ok(frame):
        return None

    flags = frame[2]
    hours = tuple(
        hour for hour in range(24) if frame[15 + hour // 8] & (1 << (hour % 8))
    )
    return HeaterStatus(
        raw=bytes(frame),
        run_state=_run_state(flags),
        fan=bool(flags & (1 << 2)),
        fuel_pump=bool(flags & (1 << 3)),
        glow_plug=bool(flags & (1 << 4)),
        flame=bool(flags & (1 << 5)),
        intake=bool(flags & (1 << 6)),
        exhaust=bool(flags & (1 << 7)),
        fault_code=None if frame[3] == NO_FAULT else frame[3],
        efficiency=min(frame[4], 99),
        actual_output_kw=round(_s8(frame[5]) / 10, 1),
        output_setting_kw=round(_s8(frame[12]) / 10, 1),
        voltage=round(frame[14] * 0.2, 1),
        total_run_hours=frame[9] | (frame[10] << 8),
        schedule_enabled=bool(frame[11] & 0b10),
        schedule_hours=hours,
    )


def parse_firmware(frame: bytes) -> str | None:
    """Decode a firmware reply into ``V<major>.<minor>.<patch>``."""
    if len(frame) < FIRMWARE_MIN_LENGTH or frame[1] != FRAME_FIRMWARE:
        return None
    if not checksum_ok(frame):
        return None
    return f"V{frame[5]}.{frame[6]}.{frame[7]}"
