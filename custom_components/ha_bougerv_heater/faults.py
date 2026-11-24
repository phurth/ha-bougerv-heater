"""Heater fault codes, possible causes, and recovery steps."""

from __future__ import annotations

from dataclasses import dataclass

_CONTACT_SUPPORT = "Contact BougeRV after-sales customer service for repair"
_MAINBOARD_DAMAGE = "Mainboard damage"
_STARTING_POWER = "Check whether the power supply meets the 150 W starting power"


@dataclass(frozen=True, slots=True)
class Fault:
    """One fault code as the heater reports it."""

    code: int
    label: str
    name: str
    causes: tuple[str, ...]
    recovery: tuple[str, ...]


FAULTS: dict[int, Fault] = {
    fault.code: fault
    for fault in (
        Fault(
            0,
            "E0",
            "Motherboard failure",
            ("The mainboard is dusty or the electronic components are damaged",),
            ("Clean the dust or replace the motherboard",),
        ),
        Fault(
            1,
            "E1",
            "Startup failure",
            (
                "Fuel exhaustion",
                "There are large bubbles in the fuel line",
                "The fuel pump does not deliver fuel or delivers little fuel",
                "White smoke",
                "Exhaust pipe dripping fuel",
            ),
            (
                "Add fuel",
                _CONTACT_SUPPORT,
                "Contact after-sales customer service to repair or replace the fuel pump",
                "Check whether the inlet and exhaust pipes and the combustion holes of "
                "the ignition plug are blocked, and clear them",
                _STARTING_POWER,
            ),
        ),
        Fault(
            2,
            "E2",
            "Flame extinguished",
            ("Intake and exhaust are not clear", "Fuel pump problem"),
            (
                "Check whether the inlet and exhaust pipes are unobstructed",
                "The fuel pump flow is insufficient or unstable. Contact after-sales "
                "customer service to repair or replace the fuel pump",
            ),
        ),
        Fault(
            3,
            "E3",
            "Abnormal voltage",
            (
                "Voltage mismatch",
                "Poor contact",
                "Additional wiring",
                "Starting power is not met",
            ),
            (
                "Check whether the heater voltage matches the power supply voltage. "
                "If not, replace the power supply",
                "Check whether the wiring harness and power supply are firmly connected",
                "Check the specifications of the power supply wiring harness",
                _STARTING_POWER,
            ),
        ),
        Fault(
            4,
            "E4",
            "Premature ignition",
            ("There is residual fuel in the heater",),
            (
                "The last abnormal shutdown left fuel inside the heater, causing it to "
                "ignite prematurely. Restart the heater after it has shut down",
            ),
        ),
        Fault(
            6,
            "E6",
            "Mainboard sensor failure",
            ("The mainboard temperature sensor is damaged or overheated",),
            (
                "Check whether the heater is overheated or the air inlet temperature is "
                "too high. If there is no overheating, replace the mainboard",
            ),
        ),
        Fault(
            7,
            "E7",
            "Fuel pump failure",
            ("Connection line is broken", "Fuel pump is damaged", _MAINBOARD_DAMAGE),
            (
                "Check whether the fuel pump harness and fuel pump socket are loose, "
                "then reconnect them securely",
                "Replace the fuel pump",
                "If neither of the above fixes it, replace the mainboard",
            ),
        ),
        Fault(
            8,
            "E8",
            "Motherboard failure",
            (
                "Fan blades are jammed",
                "Component is damaged",
                "Motor stall",
                "The connecting line is short-circuited or broken",
            ),
            (
                "Check whether the shell is deformed and return it to its original "
                "position. The fan blades may be stuck or damaged from shipping",
                _CONTACT_SUPPORT,
                "Check whether the sensor is bent or damaged, and whether the distance "
                "between the fan and the motherboard is too large, and return them to "
                "their original position",
                "The motor harness sockets on the circuit board are loose. Reconnect "
                "them securely",
            ),
        ),
        Fault(
            9,
            "E9",
            "Glow plug failure",
            ("Short circuit of the ignition plug", _MAINBOARD_DAMAGE),
            (
                "Check whether the positive and negative electrodes of the ignition plug "
                "are touching. If so, separate them. If not, replace the ignition plug",
                _CONTACT_SUPPORT,
            ),
        ),
        Fault(
            10,
            "E10",
            "Overheating",
            ("The air inlet and outlet are blocked", "The temperature sensor is damaged"),
            (
                "Check whether the air inlet and outlet of the heater are blocked, and "
                "clear them",
                _CONTACT_SUPPORT,
            ),
        ),
        Fault(
            11,
            "E11",
            "Heater sensor failure",
            ("Poor contact", "The sensor itself is faulty"),
            ("Check whether the sensor plug is loose and reconnect it firmly", _CONTACT_SUPPORT),
        ),
        Fault(
            12,
            "E12",
            "Ignition plug disconnected",
            ("Poor contact", _MAINBOARD_DAMAGE),
            (
                "Check whether the ignition plug is loose and reconnect it firmly",
                _CONTACT_SUPPORT,
            ),
        ),
        Fault(
            16,
            "C4",
            "Control panel failure",
            ("The electronic parts of the switch are overheated or damaged",),
            (
                "Check whether the control panel is affected by high temperature and "
                "keep it away from the heater. If that does not solve it, contact "
                "after-sales customer service for repair",
            ),
        ),
        Fault(
            17,
            "C7/CONN",
            "Control panel or main board failure",
            ("Switch signal transmission failure", "Switch electronic parts are damaged"),
            (
                "Check whether the control panel plug is loose and reconnect it firmly",
                _CONTACT_SUPPORT,
            ),
        ),
    )
}


def describe_fault(code: int) -> Fault:
    """Return the fault for ``code``, or a generic entry for an undefined code."""
    return FAULTS.get(code) or Fault(code, f"E{code}", "Unknown fault", (), ())
