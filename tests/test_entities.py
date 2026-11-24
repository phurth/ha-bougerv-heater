"""Entities and availability, fed with status frames instead of a radio."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_bougerv_heater.const import DOMAIN
from custom_components.ha_bougerv_heater.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ha_bougerv_heater.protocol import checksum

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "mock_bluetooth")

ADDRESS = "AA:BB:CC:DD:EE:01"
OFF_FRAME = bytes.fromhex("80 41 00 ff 00 00 3d 00 00 00 00 02 32 18 00 00 00 00 00 49")


def _frame(**bytes_at: int) -> bytes:
    frame = bytearray(OFF_FRAME)
    for name, value in bytes_at.items():
        frame[int(name.removeprefix("b"))] = value
    frame[-1] = checksum(bytes(frame[:-1]))
    return bytes(frame)


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, title="BougeRV Diesel Heater EE01", data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    with patch("custom_components.ha_bougerv_heater.coordinator.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entity_id(hass, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{ADDRESS}_{key}")
    assert entity_id is not None, key
    return entity_id


def _state(hass, platform: str, key: str):
    return hass.states.get(_entity_id(hass, platform, key))


async def test_entities_are_unavailable_until_status_arrives(hass, entry) -> None:
    assert _state(hass, "sensor", "run_state").state == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "fault").state == STATE_UNAVAILABLE


async def test_off_frame_populates_entities(hass, entry) -> None:
    entry.runtime_data.handle_status_frame(OFF_FRAME)
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "run_state").state == "off"
    assert _state(hass, "sensor", "heat_output_setting").state == "5.0"
    assert _state(hass, "sensor", "heat_output").state == "0.0"
    # The heater reports 0.0 V while off; that is not a real reading.
    assert _state(hass, "sensor", "input_voltage").state == STATE_UNKNOWN
    assert _state(hass, "sensor", "fault_code").state == "none"
    assert _state(hass, "binary_sensor", "fault").state == "off"
    schedule = _state(hass, "binary_sensor", "onboard_schedule")
    assert schedule.state == "on"
    assert schedule.attributes["hours"] == []
    assert _state(hass, "binary_sensor", "flame").state == "off"


async def test_fault_has_name_causes_and_recovery_steps(hass, entry) -> None:
    entry.runtime_data.handle_status_frame(_frame(b3=3))
    await hass.async_block_till_done()

    fault = _state(hass, "sensor", "fault_code")
    assert fault.state == "E3"
    assert fault.attributes["fault"] == "Abnormal voltage"
    assert "Voltage mismatch" in fault.attributes["possible_causes"]
    assert len(fault.attributes["recovery_steps"]) == 4
    assert _state(hass, "binary_sensor", "fault").state == "on"


async def test_running_frame(hass, entry) -> None:
    entry.runtime_data.handle_status_frame(
        _frame(b2=0b1110_1101, b4=86, b5=34, b12=34, b14=63, b9=12)
    )
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "run_state").state == "running"
    assert _state(hass, "sensor", "input_voltage").state == "12.6"
    assert _state(hass, "sensor", "combustion_efficiency").state == "86"
    assert _state(hass, "sensor", "total_run_time").state == "12"
    assert _state(hass, "binary_sensor", "flame").state == "on"
    assert _state(hass, "binary_sensor", "glow_plug").state == "off"


async def test_corrupt_frame_is_ignored(hass, entry) -> None:
    coordinator = entry.runtime_data
    coordinator.handle_status_frame(OFF_FRAME)
    corrupted = bytearray(_frame(b2=0b0010_0001))
    corrupted[-1] ^= 0xFF
    coordinator.handle_status_frame(bytes(corrupted))
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "run_state").state == "off"
    assert coordinator.stats.bad_frames == 1


async def test_lost_link_makes_entities_unavailable_not_off(hass, entry) -> None:
    coordinator = entry.runtime_data
    coordinator.handle_status_frame(_frame(b2=0b0010_0001))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "run_state").state == "running"

    coordinator._mark_unavailable()
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "run_state").state == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "flame").state == STATE_UNAVAILABLE

    # The same frame after reconnecting must restore the entities.
    coordinator.handle_status_frame(_frame(b2=0b0010_0001))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "run_state").state == "running"


async def test_firmware_reply_updates_the_device(hass, entry) -> None:
    entry.runtime_data._on_command_reply(None, bytearray.fromhex("40 0e 01 01 01 02 01 02 56"))
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [device.sw_version for device in devices] == ["V2.1.2"]


async def test_diagnostics_redact_the_address(hass, entry) -> None:
    entry.runtime_data.handle_status_frame(OFF_FRAME)
    payload = await async_get_config_entry_diagnostics(hass, entry)
    text = repr(payload)
    assert ADDRESS not in text
    assert "EE01" not in text
    assert payload["status"]["raw"] == OFF_FRAME.hex(" ")
    assert payload["status"]["unknown_fields"]["byte_6"] == 61
