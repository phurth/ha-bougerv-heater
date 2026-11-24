"""Phase 2 controls: guards, command frames, confirmation, and entities."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_bougerv_heater.const import CHAR_WRITE, DOMAIN
from custom_components.ha_bougerv_heater.coordinator import CommandError
from custom_components.ha_bougerv_heater.guards import (
    check_heat_output,
    check_power_off,
    check_power_on,
)
from custom_components.ha_bougerv_heater.protocol import (
    checksum,
    heat_output_command,
    kw_to_raw,
    parse_status,
    power_command,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "mock_bluetooth")

ADDRESS = "AA:BB:CC:DD:EE:01"
MODULE = "custom_components.ha_bougerv_heater.coordinator"
OFF_FRAME = bytes.fromhex("80 41 00 ff 00 00 3d 00 00 00 00 02 32 18 00 00 00 00 00 49")

# Byte 2 values captured during a real run.
STARTING_EARLY = 0x55  # on, fan, intake, glow plug
STARTING_LIT = 0xFD  # on, flame lit, glow plug still on
RUNNING = 0xED  # on, flame, glow plug off
SHUTTING_DOWN = 0xB6  # cool-down with glow plug burn-off


def frame(flags: int = 0, fault: int = 0xFF, setting: int = 50) -> bytes:
    data = bytearray(OFF_FRAME)
    data[2], data[3], data[12] = flags, fault, setting
    data[-1] = checksum(bytes(data[:-1]))
    return bytes(data)


def status(**kwargs):
    parsed = parse_status(frame(**kwargs))
    assert parsed is not None
    return parsed


# ------------------------------------------------------------------- guards


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({}, None),
        ({"fault": 3}, "fault_active"),
        ({"flags": SHUTTING_DOWN}, "cooling_down"),
        ({"flags": RUNNING}, "already_on"),
        ({"flags": STARTING_EARLY}, "already_on"),
    ],
)
def test_power_on_guard(kwargs, reason) -> None:
    assert check_power_on(status(**kwargs)) == reason


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"flags": RUNNING}, None),
        ({"flags": RUNNING, "fault": 2}, None),
        ({"flags": STARTING_EARLY, "fault": 1}, None),  # on with a fault: may stop
        ({"flags": STARTING_EARLY}, "starting"),
        ({"flags": STARTING_LIT}, "starting"),
        ({"flags": 0x03}, "starting"),
        ({"flags": SHUTTING_DOWN}, "cooling_down"),
        ({}, "already_off"),
    ],
)
def test_power_off_guard(kwargs, reason) -> None:
    assert check_power_off(status(**kwargs)) == reason


@pytest.mark.parametrize(
    ("kwargs", "raw", "reason"),
    [
        ({"flags": RUNNING}, 30, None),
        ({"flags": STARTING_EARLY}, 14, None),
        ({"flags": RUNNING}, 13, "output_out_of_range"),
        ({"flags": RUNNING}, 51, "output_out_of_range"),
        ({}, 30, "output_heater_off"),
        ({"flags": SHUTTING_DOWN}, 30, "output_heater_off"),
        ({"flags": RUNNING, "fault": 3}, 30, "fault_active"),
    ],
)
def test_heat_output_guard(kwargs, raw, reason) -> None:
    assert check_heat_output(status(**kwargs), raw) == reason


def test_command_frames() -> None:
    assert power_command(True).hex(" ") == "00 05 01 06"
    assert power_command(False).hex(" ") == "00 05 00 05"
    assert heat_output_command(30).hex(" ") == "00 07 1e 25"
    assert heat_output_command(14).hex(" ") == "00 07 0e 15"
    assert heat_output_command(50).hex(" ") == "00 07 32 39"
    with pytest.raises(ValueError):
        heat_output_command(51)
    assert [kw_to_raw(v) for v in (1.4, 3.0, 2.9999999, 5.0)] == [14, 30, 30, 50]


# --------------------------------------------------------------- coordinator


class FakeClient:
    """Records writes and answers with the frame the real heater would send."""

    def __init__(self, coordinator, reply: bytes | None) -> None:
        self.coordinator = coordinator
        self.reply = reply
        self.is_connected = True
        self.writes: list[bytes] = []

    async def write_gatt_char(self, char: str, data: bytes, response: bool) -> None:
        assert char == CHAR_WRITE and response is True
        self.writes.append(bytes(data))
        if self.reply is not None:
            asyncio.get_running_loop().call_later(
                0.05, self.coordinator.handle_status_frame, self.reply
            )


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, title="BougeRV Diesel Heater EE01", data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    with patch(f"{MODULE}.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def connect(entry, current: bytes, reply: bytes | None) -> FakeClient:
    coordinator = entry.runtime_data
    coordinator.handle_status_frame(current)
    client = FakeClient(coordinator, reply)
    coordinator._client = client
    return client


async def test_power_on_sends_once_and_waits_for_starting(hass, entry) -> None:
    client = connect(entry, frame(), reply=frame(flags=0x03))
    await entry.runtime_data.async_set_power(True)
    assert client.writes == [bytes.fromhex("00 05 01 06")]


async def test_power_off_while_running(hass, entry) -> None:
    client = connect(entry, frame(flags=RUNNING), reply=frame(flags=SHUTTING_DOWN))
    await entry.runtime_data.async_set_power(False)
    assert client.writes == [bytes.fromhex("00 05 00 05")]


@pytest.mark.parametrize(
    ("current", "on", "reason"),
    [
        (frame(fault=3), True, "fault_active"),
        (frame(flags=SHUTTING_DOWN), True, "cooling_down"),
        (frame(flags=STARTING_LIT), False, "starting"),
    ],
)
async def test_refused_commands_write_nothing(hass, entry, current, on, reason) -> None:
    client = connect(entry, current, reply=None)
    with pytest.raises(CommandError) as err:
        await entry.runtime_data.async_set_power(on)
    assert err.value.reason == reason
    assert client.writes == []


@pytest.mark.parametrize(
    ("current", "on"),
    [
        (frame(flags=RUNNING), True),
        (frame(flags=STARTING_EARLY), True),
        (frame(), False),
        (frame(flags=SHUTTING_DOWN), False),
    ],
)
async def test_commands_already_in_effect_are_not_sent(hass, entry, current, on) -> None:
    client = connect(entry, current, reply=None)
    await entry.runtime_data.async_set_power(on)
    assert client.writes == []


async def test_heat_output_is_sent_and_confirmed(hass, entry) -> None:
    client = connect(entry, frame(flags=RUNNING), reply=frame(flags=RUNNING, setting=30))
    await entry.runtime_data.async_set_heat_output(3.0)
    assert client.writes == [bytes.fromhex("00 07 1e 25")]
    assert entry.runtime_data.data.output_setting_kw == 3.0


async def test_heat_output_refused_while_off(hass, entry) -> None:
    client = connect(entry, frame(), reply=None)
    with pytest.raises(CommandError) as err:
        await entry.runtime_data.async_set_heat_output(3.0)
    assert err.value.reason == "output_heater_off"
    assert client.writes == []


async def test_unconfirmed_command_is_reported_and_not_retried(hass, entry) -> None:
    # The heater keeps reporting Off after the power-on command.
    client = connect(entry, frame(), reply=frame())
    with patch(f"{MODULE}.POWER_CONFIRM_TIMEOUT", 0.3), pytest.raises(CommandError) as err:
        await entry.runtime_data.async_set_power(True)
    assert err.value.reason == "not_confirmed"
    assert client.writes == [bytes.fromhex("00 05 01 06")]
    assert entry.runtime_data._waiters == []


async def test_commands_need_a_live_connection(hass, entry) -> None:
    coordinator = entry.runtime_data
    coordinator.handle_status_frame(frame())
    with pytest.raises(CommandError) as err:
        await coordinator.async_set_power(True)
    assert err.value.reason == "not_connected"

    client = connect(entry, frame(), reply=None)
    coordinator._mark_unavailable()
    with pytest.raises(CommandError) as err:
        await coordinator.async_set_power(True)
    assert err.value.reason == "not_connected"
    assert client.writes == []


# ------------------------------------------------------------------ entities


async def test_controls_are_disabled_by_default(hass, entry) -> None:
    registry = er.async_get(hass)
    for platform, key in (("switch", "power"), ("number", "heat_output_control")):
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{ADDRESS}_{key}")
        assert entity_id is not None
        assert registry.async_get(entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def _enable_controls(hass, entry) -> tuple[str, str]:
    registry = er.async_get(hass)
    ids = []
    for platform, key in (("switch", "power"), ("number", "heat_output_control")):
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{ADDRESS}_{key}")
        registry.async_update_entity(entity_id, disabled_by=None)
        ids.append(entity_id)
    with patch(f"{MODULE}.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    return ids[0], ids[1]


async def test_switch_and_number_follow_status_and_send_commands(hass, entry) -> None:
    switch_id, number_id = await _enable_controls(hass, entry)
    client = connect(entry, frame(flags=RUNNING, setting=40), reply=frame(flags=RUNNING, setting=25))
    await hass.async_block_till_done()
    assert hass.states.get(switch_id).state == "on"
    assert hass.states.get(number_id).state == "4.0"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 2.5}, blocking=True
    )
    assert client.writes == [bytes.fromhex("00 07 19 20")]
    assert hass.states.get(number_id).state == "2.5"


async def test_refusal_is_shown_as_a_translated_error(hass, entry) -> None:
    switch_id, _ = await _enable_controls(hass, entry)
    client = connect(entry, frame(fault=3), reply=None)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_id}, blocking=True
        )
    assert err.value.translation_key == "fault_active"
    assert client.writes == []


async def test_lost_link_is_an_error_not_a_validation_problem(hass, entry) -> None:
    switch_id, _ = await _enable_controls(hass, entry)
    entry.runtime_data.handle_status_frame(frame(flags=RUNNING))
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "not_connected"
