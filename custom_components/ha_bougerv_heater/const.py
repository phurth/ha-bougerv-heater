"""Constants for the BougeRV diesel heater integration."""

from __future__ import annotations

DOMAIN = "ha_bougerv_heater"
MANUFACTURER = "BougeRV"
MODEL = "Portable Diesel Heater"
TITLE = "BougeRV Diesel Heater"

SERVICE_UUID = "43f4b114-ca67-48e8-a46f-9a8ffeb7146a"
CHAR_WRITE = "bf83f3f1-399a-414d-9035-ce64ceb3ff67"
CHAR_COMMAND_NOTIFY = "bf83f3f2-399a-414d-9035-ce64ceb3ff67"
CHAR_STATUS_NOTIFY = "bf83f3f3-399a-414d-9035-ce64ceb3ff67"

# Advertised name prefixes. The Pro uses the same UUIDs, so the name is the only
# way to tell the models apart. Keep this list exact: a wildcard such as
# "BougeRV-DHG00*" would also match the Pro's "BougeRV-DHG005".
STANDARD_NAME_PREFIXES = (
    "BougeRV-DHG001",
    "BougeRV-DHG002",
    "BougeRV-DHG003",
    "BougeRV-DHG004",
    "LFB00000",
)

CONF_MAC_CONFIRMED = "mac_confirmed"

# Thermostat options. Margins are stored in °C.
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_MIN_RUN_MINUTES = "min_run_minutes"
CONF_MIN_OFF_MINUTES = "min_off_minutes"
CONF_STOP_ABOVE = "stop_above"
CONF_RESTART_BELOW = "restart_below"
CONF_SENSOR_TIMEOUT_MINUTES = "sensor_timeout_minutes"

DEFAULT_MIN_RUN_MINUTES = 15
DEFAULT_MIN_OFF_MINUTES = 10
DEFAULT_STOP_ABOVE = 2.0
DEFAULT_RESTART_BELOW = 1.0
DEFAULT_SENSOR_TIMEOUT_MINUTES = 15

THERMOSTAT_EVALUATE_INTERVAL = 30.0

# Status frames arrive about once a second.
STALE_AFTER = 10.0
FIRMWARE_RETRY_DELAY = 0.3
FIRMWARE_REPLY_TIMEOUT = 3.0
NOTIFY_SWITCH_DELAY = 0.4
RECONNECT_AFTER_DROP = 2.0
RECONNECT_MIN = 5.0
RECONNECT_MAX = 60.0
NOT_IN_RANGE_RECHECK = 300.0
POWER_CONFIRM_TIMEOUT = 10.0
OUTPUT_CONFIRM_TIMEOUT = 6.0


def is_standard_heater_name(name: str | None) -> bool:
    """Return True for a standard (non-Pro) heater's advertised name."""
    return bool(name) and name.startswith(STANDARD_NAME_PREFIXES)
