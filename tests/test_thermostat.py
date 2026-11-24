"""Thermostat decisions, without Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.ha_bougerv_heater.protocol import checksum, parse_status
from custom_components.ha_bougerv_heater.thermostat import (
    Action,
    Lockout,
    Settings,
    ThermostatController,
    output_for,
)

OFF_FRAME = bytes.fromhex("80 41 00 ff 00 00 3d 00 00 00 00 02 32 18 00 00 00 00 00 49")
STARTING = 0x55
RUNNING = 0xED
SHUTTING_DOWN = 0xB6
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def status(flags: int = 0, fault: int = 0xFF, setting: int = 50):
    data = bytearray(OFF_FRAME)
    data[2], data[3], data[12] = flags, fault, setting
    data[-1] = checksum(bytes(data[:-1]))
    parsed = parse_status(bytes(data))
    assert parsed is not None
    return parsed


def heating(target: float = 20.0) -> ThermostatController:
    controller = ThermostatController(settings=Settings())
    controller.set_heating(True, heater_on=False)
    controller.target = target
    return controller


def test_output_curve() -> None:
    assert output_for(20.0, 20.5) == 1.4
    assert output_for(20.0, 20.0) == 1.4
    assert output_for(20.0, 18.5) == 3.2
    assert output_for(20.0, 17.0) == 5.0
    assert output_for(20.0, 5.0) == 5.0


def test_starts_only_below_the_restart_margin() -> None:
    controller = heating()
    assert controller.decide(T0, status(), 19.5, False).action is Action.NONE
    assert controller.decide(T0, status(), 19.0, False).action is Action.START


def test_minimum_off_time_delays_a_restart() -> None:
    controller = heating()
    controller.last_stop = T0
    decision = controller.decide(T0 + timedelta(minutes=9), status(), 15.0, False)
    assert (decision.action, decision.reason) == (Action.NONE, "waiting for the minimum off time")
    assert controller.decide(T0 + timedelta(minutes=10), status(), 15.0, False).action is Action.START


def test_modulates_output_while_running() -> None:
    controller = heating()
    controller.started(T0)
    decision = controller.decide(T0 + timedelta(minutes=5), status(RUNNING, setting=50), 19.0, False)
    assert decision.action is Action.SET_OUTPUT
    assert decision.output_kw == 2.6

    controller.output_changed(T0 + timedelta(minutes=5))
    # Within the modulation interval nothing more is sent.
    soon = controller.decide(T0 + timedelta(minutes=5, seconds=30), status(RUNNING, setting=26), 18.0, False)
    assert soon.action is Action.NONE
    # Small changes are ignored.
    later = controller.decide(T0 + timedelta(minutes=7), status(RUNNING, setting=26), 19.05, False)
    assert later.action is Action.NONE


def test_stops_only_above_margin_at_minimum_output_after_minimum_run() -> None:
    controller = heating()
    controller.started(T0)
    # Above the margin but not yet at minimum output: turn output down first.
    first = controller.decide(T0 + timedelta(minutes=20), status(RUNNING, setting=30), 22.5, False)
    assert (first.action, first.output_kw) == (Action.SET_OUTPUT, 1.4)
    # At minimum output but inside the minimum run time: keep running.
    early = controller.decide(T0 + timedelta(minutes=10), status(RUNNING, setting=14), 22.5, False)
    assert (early.action, early.reason) == (Action.NONE, "waiting for the minimum run time")
    # Above the margin since 0:20 at minimum output: hold for the 3-minute dwell.
    hold = controller.decide(T0 + timedelta(minutes=22), status(RUNNING, setting=14), 22.0, False)
    assert (hold.action, hold.reason) == (Action.NONE, "holding minimum output before stopping")
    late = controller.decide(T0 + timedelta(minutes=23), status(RUNNING, setting=14), 22.0, False)
    assert late.action is Action.STOP
    # Below the stop margin at minimum output: keep running.
    warm = controller.decide(T0 + timedelta(minutes=23), status(RUNNING, setting=14), 21.9, False)
    assert warm.action is Action.NONE


def test_brief_spike_does_not_stop_the_heater() -> None:
    controller = heating()
    controller.started(T0)
    at_min = status(RUNNING, setting=14)
    t = T0 + timedelta(minutes=30)
    assert controller.decide(t, at_min, 23.0, False).action is Action.NONE  # spike begins
    t += timedelta(minutes=2)
    assert controller.decide(t, at_min, 20.5, False).action is Action.NONE  # back below margin
    t += timedelta(minutes=2)
    # Above again: the dwell starts over, so 4 minutes after the first spike is not enough.
    assert controller.decide(t, at_min, 23.0, False).action is Action.NONE
    t += timedelta(minutes=2, seconds=59)
    assert controller.decide(t, at_min, 23.0, False).action is Action.NONE
    t += timedelta(seconds=1)
    assert controller.decide(t, at_min, 23.0, False).action is Action.STOP


def test_sensor_gap_restarts_the_dwell() -> None:
    controller = heating()
    controller.started(T0)
    at_min = status(RUNNING, setting=14)
    t = T0 + timedelta(minutes=30)
    controller.decide(t, at_min, 23.0, False)
    controller.decide(t + timedelta(minutes=1), at_min, None, False)  # no reading
    assert controller.decide(t + timedelta(minutes=3), at_min, 23.0, False).action is Action.NONE
    assert controller.decide(t + timedelta(minutes=6), at_min, 23.0, False).action is Action.STOP


def test_thermostat_off_stops_a_running_heater_once_it_can() -> None:
    controller = heating()
    controller.set_heating(False, heater_on=True)
    starting = controller.decide(T0, status(STARTING), 15.0, False)
    assert starting.action is Action.NONE  # power off is refused during startup
    assert controller.decide(T0, status(RUNNING), 15.0, False).action is Action.STOP
    controller.stopping()
    controller.stopped(T0)
    assert controller.decide(T0, status(SHUTTING_DOWN), 15.0, False).action is Action.NONE


def test_starts_made_elsewhere_are_left_alone_while_off() -> None:
    controller = ThermostatController()
    controller.observe(status(), T0)
    controller.observe(status(RUNNING), T0)
    assert controller.decide(T0, status(RUNNING), 30.0, False).action is Action.NONE


def test_stop_made_elsewhere_turns_the_thermostat_off() -> None:
    controller = heating()
    controller.observe(status(RUNNING), T0)
    assert controller.observe(status(SHUTTING_DOWN), T0) is True
    assert controller.heating is False
    assert controller.last_stop == T0


def test_our_own_stop_does_not_turn_the_thermostat_off() -> None:
    controller = heating()
    controller.started(T0)
    controller.observe(status(RUNNING), T0)
    controller.stopping()
    # The confirming frame arrives while the stop command is still in flight.
    assert controller.observe(status(SHUTTING_DOWN), T0 + timedelta(minutes=20)) is False
    controller.stopped(T0 + timedelta(minutes=20))
    assert controller.heating is True


def test_link_drop_is_not_mistaken_for_a_stop() -> None:
    controller = heating()
    controller.observe(status(RUNNING), T0)
    controller.forget_heater()
    assert controller.observe(status(), T0) is False
    assert controller.heating is True


def test_fault_locks_out_until_re_enabled() -> None:
    controller = heating()
    controller.observe(status(RUNNING), T0)
    assert controller.observe(status(SHUTTING_DOWN, fault=2), T0) is False
    assert controller.heating is True
    assert controller.lockout is Lockout.FAULT
    # Even once the fault clears, nothing happens until the user re-enables.
    assert controller.decide(T0 + timedelta(hours=1), status(), 10.0, False).action is Action.NONE
    controller.set_heating(False, heater_on=False)
    controller.set_heating(True, heater_on=False)
    assert controller.lockout is None
    assert controller.decide(T0 + timedelta(hours=1), status(), 10.0, False).action is Action.START


def test_unconfirmed_start_locks_out() -> None:
    controller = heating()
    controller.start_failed()
    decision = controller.decide(T0, status(), 10.0, False)
    assert (decision.action, decision.reason) == (Action.NONE, "locked out: start_failed")


@pytest.mark.parametrize(
    ("flags", "action"),
    [(RUNNING, Action.STOP), (STARTING, Action.NONE), (0, Action.NONE), (SHUTTING_DOWN, Action.NONE)],
)
def test_stale_sensor_shuts_down_a_running_heater(flags: int, action: Action) -> None:
    controller = heating()
    assert controller.decide(T0, status(flags), None, True).action is action


def test_missing_reading_waits_without_stopping() -> None:
    controller = heating()
    decision = controller.decide(T0, status(RUNNING), None, False)
    assert (decision.action, decision.reason) == (Action.NONE, "waiting for the temperature sensor")


def test_nothing_happens_without_heater_status() -> None:
    controller = heating()
    assert controller.decide(T0, None, 10.0, False).reason == "heater unavailable"
