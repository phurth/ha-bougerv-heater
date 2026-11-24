"""Thermostat entity and options flow inside Home Assistant."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_bougerv_heater.const import (
    CONF_MIN_OFF_MINUTES,
    CONF_MIN_RUN_MINUTES,
    CONF_RESTART_BELOW,
    CONF_SENSOR_TIMEOUT_MINUTES,
    CONF_STOP_ABOVE,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
)

from .test_controls import FakeClient, frame

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "mock_bluetooth")

ADDRESS = "AA:BB:CC:DD:EE:01"
MODULE = "custom_components.ha_bougerv_heater.coordinator"
CLIMATE = "custom_components.ha_bougerv_heater.climate"
SENSOR = "sensor.room_temperature"
RUNNING = 0xED
SHUTTING_DOWN = 0xB6
OPTIONS = {
    CONF_TEMPERATURE_SENSOR: SENSOR,
    CONF_MIN_RUN_MINUTES: 15,
    CONF_MIN_OFF_MINUTES: 10,
    CONF_STOP_ABOVE: 2.0,
    CONF_RESTART_BELOW: 1.0,
    CONF_SENSOR_TIMEOUT_MINUTES: 15,
}


def set_room(hass, fahrenheit: float | str) -> None:
    hass.states.async_set(
        SENSOR, str(fahrenheit), {"unit_of_measurement": "°F", "device_class": "temperature"}
    )


async def setup(hass, options: dict | None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title="BougeRV Diesel Heater EE01",
        data={CONF_ADDRESS: ADDRESS},
        options=options or {},
    )
    entry.add_to_hass(hass)
    with patch(f"{MODULE}.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def enable_thermostat(hass, entry) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("climate", DOMAIN, f"{ADDRESS}_thermostat")
    assert entity_id is not None
    assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    registry.async_update_entity(entity_id, disabled_by=None)
    with patch(f"{MODULE}.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    return entity_id


def connect(entry, current: bytes, reply: bytes | None) -> FakeClient:
    coordinator = entry.runtime_data
    coordinator.handle_status_frame(current)
    client = FakeClient(coordinator, reply)
    coordinator._client = client
    return client


async def test_no_thermostat_without_a_sensor(hass) -> None:
    await setup(hass, None)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("climate", DOMAIN, f"{ADDRESS}_thermostat") is None


async def test_options_flow_shows_and_stores_margins(hass) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_room(hass, 68)
    entry = await setup(hass, None)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema if hasattr(key, "default") and callable(key.default)}
    assert defaults[CONF_STOP_ABOVE] == 3.6  # 2 °C shown in °F
    assert defaults[CONF_RESTART_BELOW] == 1.8

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_TEMPERATURE_SENSOR: SENSOR,
            CONF_MIN_RUN_MINUTES: 20,
            CONF_MIN_OFF_MINUTES: 10,
            CONF_STOP_ABOVE: 4.5,
            CONF_RESTART_BELOW: 1.8,
            CONF_SENSOR_TIMEOUT_MINUTES: 15,
        },
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_STOP_ABOVE] == 2.5  # stored in °C
    assert entry.options[CONF_RESTART_BELOW] == 1.0
    assert entry.options[CONF_MIN_RUN_MINUTES] == 20
    # The thermostat now exists (disabled by default).
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("climate", DOMAIN, f"{ADDRESS}_thermostat") is not None


async def test_heat_mode_starts_a_cold_room(hass) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_room(hass, 50)  # 10 °C
    entry = await setup(hass, OPTIONS)
    entity_id = await enable_thermostat(hass, entry)
    client = connect(entry, frame(), reply=frame(flags=0x03))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    assert client.writes == []

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 68, "hvac_mode": "heat"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert client.writes == [bytes.fromhex("00 05 01 06")]
    state = hass.states.get(entity_id)
    assert state.state == "heat"
    assert state.attributes["temperature"] == 68  # shown in the system unit
    assert state.attributes["hvac_action"] == "heating"


async def test_stop_made_elsewhere_turns_the_thermostat_off(hass) -> None:
    set_room(hass, 65)  # 18.3 °C: at the default 18 °C target, so output stays at minimum
    entry = await setup(hass, OPTIONS)
    entity_id = await enable_thermostat(hass, entry)
    client = connect(entry, frame(flags=RUNNING, setting=14), reply=None)
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "heat"

    entry.runtime_data.handle_status_frame(frame(flags=SHUTTING_DOWN, setting=14))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    assert all(write[1] != 0x05 for write in client.writes)  # we never sent power


async def test_turning_the_thermostat_off_stops_the_heater(hass) -> None:
    set_room(hass, 65)
    entry = await setup(hass, OPTIONS)
    entity_id = await enable_thermostat(hass, entry)
    client = connect(entry, frame(flags=RUNNING, setting=14), reply=frame(flags=SHUTTING_DOWN, setting=14))
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True
    )
    await hass.async_block_till_done()
    client.writes.clear()
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}, blocking=True
    )
    await hass.async_block_till_done()
    assert client.writes == [bytes.fromhex("00 05 00 05")]
    assert hass.states.get(entity_id).attributes["hvac_action"] == "fan"


async def test_stale_sensor_shuts_the_heater_down(hass) -> None:
    set_room(hass, 65)
    entry = await setup(hass, OPTIONS)
    entity_id = await enable_thermostat(hass, entry)
    client = connect(
        entry, frame(flags=RUNNING, setting=14), reply=frame(flags=SHUTTING_DOWN, setting=14)
    )
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True
    )
    await hass.async_block_till_done()
    hass.states.async_set(SENSOR, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert client.writes == []  # unavailable, but not yet for the 15-minute timeout

    # Move only the thermostat's clock forward; the fake heater's reply timer
    # runs on the real event loop clock.
    later = dt_util.utcnow() + timedelta(minutes=16)
    clock = SimpleNamespace(utcnow=lambda: later, parse_datetime=dt_util.parse_datetime)
    with patch(f"{CLIMATE}.dt_util", clock):
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": entity_id, "temperature": 18}, blocking=True
        )
        await hass.async_block_till_done()
    assert client.writes == [bytes.fromhex("00 05 00 05")]
    assert hass.states.get(entity_id).state == "heat"  # still heating mode, waiting for the sensor


async def test_onboard_schedule_with_hours_raises_a_repair(hass) -> None:
    set_room(hass, 68)
    entry = await setup(hass, OPTIONS)
    await enable_thermostat(hass, entry)
    issues = ir.async_get(hass)
    issue_id = f"onboard_schedule_{ADDRESS}"

    entry.runtime_data.handle_status_frame(frame())  # enabled, no hours
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None

    data = bytearray(frame())
    data[15] = 0x01  # 01:00
    from custom_components.ha_bougerv_heater.protocol import checksum

    data[-1] = checksum(bytes(data[:-1]))
    entry.runtime_data.handle_status_frame(bytes(data))
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders["hours"] == "01:00"

    entry.runtime_data.handle_status_frame(frame())
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id) is None


# ------------------------------------------------------------------- restore


async def test_target_survives_a_reload_in_fahrenheit(hass) -> None:
    """Regression: a 65 °F target came back as 65 °C (149 °F) after a reload."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_room(hass, 72)
    entry = await setup(hass, OPTIONS)
    entity_id = await enable_thermostat(hass, entry)
    connect(entry, frame(), reply=None)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 65, "hvac_mode": "heat"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["temperature"] == 65

    with patch(f"{MODULE}.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    # Hold the heater "on" so the restored Heat mode has nothing to start.
    entry.runtime_data.handle_status_frame(frame(flags=0xED, setting=14))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["temperature"] == 65
    assert state.state == "heat"


async def _restore(hass, last_state, extra: dict | None) -> str:
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import (
        mock_restore_cache,
        mock_restore_cache_with_extra_data,
    )

    entity_id = "climate.bougerv_diesel_heater_ee01_thermostat"
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "climate", DOMAIN, f"{ADDRESS}_thermostat", suggested_object_id="bougerv_diesel_heater_ee01_thermostat"
    )
    state = State(entity_id, last_state[0], last_state[1])
    if extra is None:
        mock_restore_cache(hass, [state])
    else:
        mock_restore_cache_with_extra_data(hass, [(state, extra)])
    return entity_id


async def test_older_save_without_target_c_is_converted_from_the_display_unit(hass) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_room(hass, 72)
    entity_id = await _restore(hass, ("heat", {"temperature": 65}), None)
    entry = await setup(hass, OPTIONS)
    entry.runtime_data.handle_status_frame(frame(flags=0xED, setting=14))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["temperature"] == 65
    assert state.state == "heat"


async def test_out_of_range_saved_target_is_clamped(hass) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_room(hass, 72)
    entity_id = await _restore(
        hass, ("off", {"temperature": 149}), {"target_c": 65.0, "last_start": None, "last_stop": None}
    )
    entry = await setup(hass, OPTIONS)
    entry.runtime_data.handle_status_frame(frame())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["temperature"] == 86  # 30 °C maximum
