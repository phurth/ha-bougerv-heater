"""When a control command may be sent. Pure functions, no Home Assistant imports.

Each check returns None when the command is allowed, or a reason key that maps
to a translated error message. The rules follow what the BougeRV app allows:

- Power on only from Off with no active fault. The app hides the power button
  while the heater is starting or cooling down.
- Power off only while running, or while commanded on with an active fault
  (the app shows the off button in that case too). Power-off during a normal
  startup is refused until its effect has been tested on the heater.
- Heat output only while starting or running with no active fault, and only
  within 1.4-5.0 kW.
"""

from __future__ import annotations

from .protocol import OUTPUT_MAX_RAW, OUTPUT_MIN_RAW, HeaterStatus, RunState


def check_power_on(status: HeaterStatus) -> str | None:
    if status.fault_active:
        return "fault_active"
    if status.run_state is RunState.SHUTTING_DOWN:
        return "cooling_down"
    if status.run_state is not RunState.OFF:
        return "already_on"
    return None


def check_power_off(status: HeaterStatus) -> str | None:
    if status.run_state is RunState.OFF:
        return "already_off"
    if status.run_state is RunState.SHUTTING_DOWN:
        return "cooling_down"
    if status.run_state is RunState.RUNNING:
        return None
    if status.fault_active and status.on_flag:
        return None
    return "starting"


def check_heat_output(status: HeaterStatus, raw: int) -> str | None:
    if not OUTPUT_MIN_RAW <= raw <= OUTPUT_MAX_RAW:
        return "output_out_of_range"
    if status.fault_active:
        return "fault_active"
    if not status.is_on:
        return "output_heater_off"
    return None
