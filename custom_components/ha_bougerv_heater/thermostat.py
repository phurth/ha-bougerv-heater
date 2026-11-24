"""Thermostat control logic. Pure Python: no Home Assistant imports, no I/O.

The climate entity feeds this controller the heater status, the room
temperature, and the time, and executes whatever single action it decides.
Temperatures are in °C.

Behavior (agreed design, see docs/TECH_SPEC.md 9.4):
1. Turning the thermostat off turns a running heater off.
2. While off, starts made elsewhere (app, heater panel) are left alone.
3. While heating, a stop made elsewhere switches the thermostat off.
4. Output is modulated first; on/off cycling is the last resort.
5. Minimum run and off times prevent short cycling.
6. A stale temperature sensor shuts the heater down. A heater fault, or a
   start the heater does not confirm, locks the thermostat out until it is
   turned off and on again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .guards import check_power_off
from .protocol import OUTPUT_MAX_RAW, OUTPUT_MIN_RAW, HeaterStatus, RunState

OUTPUT_MIN_KW = OUTPUT_MIN_RAW / 10
OUTPUT_MAX_KW = OUTPUT_MAX_RAW / 10

# Output reaches maximum when the room is this far below the target.
MODULATION_BAND = 3.0
# Smallest output change worth sending, and how often output may change.
MODULATION_STEP = 0.2
MODULATION_INTERVAL = timedelta(seconds=60)
# The room must stay above the stop margin, at minimum output, this long before
# a stop, so a brief sensor spike cannot cost a full stop and restart.
STOP_DWELL = timedelta(minutes=3)


class Action(StrEnum):
    NONE = "none"
    START = "start"
    STOP = "stop"
    SET_OUTPUT = "set_output"


class Lockout(StrEnum):
    FAULT = "fault"
    START_FAILED = "start_failed"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    output_kw: float | None = None


@dataclass(frozen=True)
class Settings:
    min_run: timedelta = timedelta(minutes=15)
    min_off: timedelta = timedelta(minutes=10)
    stop_above: float = 2.0
    restart_below: float = 1.0


def output_for(target: float, room: float) -> float:
    """Proportional output: 1.4 kW at or above target, 5.0 kW at 3 °C below."""
    fraction = min(max((target - room) / MODULATION_BAND, 0.0), 1.0)
    return round(OUTPUT_MIN_KW + fraction * (OUTPUT_MAX_KW - OUTPUT_MIN_KW), 1)


@dataclass
class ThermostatController:
    """Decides one action at a time. The caller reports what it did."""

    settings: Settings = field(default_factory=Settings)
    heating: bool = False
    target: float = 18.0
    lockout: Lockout | None = None
    last_start: datetime | None = None
    last_stop: datetime | None = None
    last_output_change: datetime | None = None
    _stop_pending: bool = False
    _expect_off: bool = False
    _prev_on: bool | None = None
    _above_since: datetime | None = None

    # ------------------------------------------------------------ user input

    def set_heating(self, heating: bool, heater_on: bool) -> None:
        """Change the thermostat mode. Re-enabling clears any lockout."""
        self.lockout = None
        self.heating = heating
        self._stop_pending = not heating and heater_on

    # ------------------------------------------------------------ observation

    def observe(self, status: HeaterStatus, now: datetime) -> bool:
        """Track the heater. Returns True if a stop made elsewhere turned us off."""
        turned_off_elsewhere = False
        on = status.is_on
        if status.fault_active and self.heating:
            self.lockout = Lockout.FAULT
        if self._prev_on and not on:
            if self.last_stop is None or not self._expect_off:
                self.last_stop = now
            if self.heating and not self._expect_off and not status.fault_active:
                self.heating = False
                turned_off_elsewhere = True
            self._expect_off = False
        self._prev_on = on
        return turned_off_elsewhere

    def forget_heater(self) -> None:
        """The link dropped. Judge the next status on its own, not as a change."""
        self._prev_on = None

    # ------------------------------------------------------------ results

    def started(self, now: datetime) -> None:
        self.last_start = now
        self._expect_off = False
        self._above_since = None

    def stopping(self) -> None:
        """Call before sending a stop: its confirming frame arrives mid-command."""
        self._expect_off = True

    def stopped(self, now: datetime) -> None:
        self.last_stop = now
        self._stop_pending = False
        self._above_since = None

    def stop_failed(self) -> None:
        self._expect_off = False

    def output_changed(self, now: datetime) -> None:
        self.last_output_change = now

    def start_failed(self) -> None:
        self.lockout = Lockout.START_FAILED

    # ------------------------------------------------------------ decision

    def decide(
        self,
        now: datetime,
        status: HeaterStatus | None,
        room: float | None,
        sensor_stale: bool,
    ) -> Decision:
        """Return the single next action."""
        if status is None:
            return Decision(Action.NONE, "heater unavailable")

        if not self.heating:
            if self._stop_pending:
                if status.run_state in (RunState.OFF, RunState.SHUTTING_DOWN):
                    self._stop_pending = False
                    return Decision(Action.NONE, "off")
                if check_power_off(status) is None:
                    return Decision(Action.STOP, "thermostat turned off")
                return Decision(Action.NONE, "waiting to stop until the heater is running")
            return Decision(Action.NONE, "off")

        if self.lockout is not None:
            return Decision(Action.NONE, f"locked out: {self.lockout.value}")
        if status.fault_active:
            self.lockout = Lockout.FAULT
            return Decision(Action.NONE, "locked out: fault")

        if sensor_stale or room is None:
            self._above_since = None
        if sensor_stale:
            if status.is_on:
                if check_power_off(status) is None:
                    return Decision(Action.STOP, "temperature sensor unavailable")
                return Decision(Action.NONE, "waiting to stop: temperature sensor unavailable")
            return Decision(Action.NONE, "waiting for the temperature sensor")
        if room is None:
            return Decision(Action.NONE, "waiting for the temperature sensor")

        state = status.run_state
        if state is not RunState.RUNNING:
            self._above_since = None
        if state is RunState.OFF:
            if room > self.target - self.settings.restart_below:
                return Decision(Action.NONE, "idle")
            if self.last_stop is not None and now - self.last_stop < self.settings.min_off:
                return Decision(Action.NONE, "waiting for the minimum off time")
            return Decision(Action.START, "below target")
        if state is RunState.SHUTTING_DOWN:
            return Decision(Action.NONE, "heater cooling down")
        if state is RunState.STARTING:
            return Decision(Action.NONE, "heater starting")

        # Running.
        setting = status.output_setting_kw
        ran = now - self.last_start if self.last_start is not None else self.settings.min_run
        above = room >= self.target + self.settings.stop_above
        if not above:
            self._above_since = None
        elif self._above_since is None:
            self._above_since = now
        if above and setting <= OUTPUT_MIN_KW:
            if ran < self.settings.min_run:
                return Decision(Action.NONE, "waiting for the minimum run time")
            if self._above_since is None or now - self._above_since < STOP_DWELL:
                return Decision(Action.NONE, "holding minimum output before stopping")
            return Decision(Action.STOP, "above target at minimum output")

        desired = output_for(self.target, room)
        change_due = (
            self.last_output_change is None
            or now - self.last_output_change >= MODULATION_INTERVAL
        )
        big_enough = abs(desired - setting) >= MODULATION_STEP - 1e-9 or (
            desired in (OUTPUT_MIN_KW, OUTPUT_MAX_KW) and desired != setting
        )
        if big_enough and change_due:
            return Decision(Action.SET_OUTPUT, "regulating", desired)
        return Decision(Action.NONE, "regulating")
