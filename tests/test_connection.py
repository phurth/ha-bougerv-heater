"""Connection session against a fake BLE client."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_ADDRESS
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_bougerv_heater.const import (
    CHAR_COMMAND_NOTIFY,
    CHAR_STATUS_NOTIFY,
    CHAR_WRITE,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations", "mock_bluetooth")

ADDRESS = "AA:BB:CC:DD:EE:01"
OFF_FRAME = bytes.fromhex("80 41 00 ff 00 00 3d 00 00 00 00 02 32 18 00 00 00 00 00 49")
FIRMWARE_FRAME = bytes.fromhex("40 0e 01 01 01 02 01 02 56")
MODULE = "custom_components.ha_bougerv_heater.coordinator"


class FakeClient:
    """Answers the firmware query and streams status like the real heater."""

    def __init__(self, disconnected_callback, *, frames: int, then_disconnect: bool) -> None:
        self._disconnected_callback = disconnected_callback
        self._frames = frames
        self._then_disconnect = then_disconnect
        self.is_connected = True
        self.calls: list[tuple] = []
        self.writes: list[bytes] = []
        self._handlers: dict[str, object] = {}

    async def start_notify(self, char: str, handler) -> None:
        self.calls.append(("start_notify", char))
        self._handlers[char] = handler
        if char == CHAR_STATUS_NOTIFY:
            asyncio.get_running_loop().create_task(self._stream())

    async def stop_notify(self, char: str) -> None:
        self.calls.append(("stop_notify", char))
        self._handlers.pop(char, None)

    async def write_gatt_char(self, char: str, data: bytes, response: bool) -> None:
        self.calls.append(("write", char, bytes(data).hex(" "), response))
        self.writes.append(bytes(data))
        handler = self._handlers.get(CHAR_COMMAND_NOTIFY)
        if handler is not None:
            handler(None, bytearray(FIRMWARE_FRAME))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect",))
        self.is_connected = False

    async def _stream(self) -> None:
        for _ in range(self._frames):
            await asyncio.sleep(0.05)
            handler = self._handlers.get(CHAR_STATUS_NOTIFY)
            if handler is not None:
                handler(None, bytearray(OFF_FRAME))
        if self._then_disconnect:
            self.is_connected = False
            self._disconnected_callback(self)


async def _coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, title="BougeRV Diesel Heater EE01", data={CONF_ADDRESS: ADDRESS}
    )
    entry.add_to_hass(hass)
    with patch(f"{MODULE}.HeaterCoordinator.async_start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry.runtime_data


async def _run_session(hass, coordinator, *, frames: int, then_disconnect: bool):
    clients: list[FakeClient] = []

    async def fake_establish(_cls, _device, _name, *, disconnected_callback, **_kwargs):
        client = FakeClient(disconnected_callback, frames=frames, then_disconnect=then_disconnect)
        clients.append(client)
        return client

    with (
        patch(f"{MODULE}.bluetooth.async_ble_device_from_address", return_value=object()),
        patch(f"{MODULE}.establish_connection", side_effect=fake_establish),
        patch(f"{MODULE}.STALE_AFTER", 0.5),
    ):
        got_data = await coordinator._session()
    return got_data, clients[0]


async def test_session_order_and_only_the_firmware_query_is_written(hass) -> None:
    coordinator = await _coordinator(hass)
    got_data, client = await _run_session(hass, coordinator, frames=3, then_disconnect=True)

    assert got_data is True
    assert coordinator.firmware == "V2.1.2"
    assert coordinator.data is not None and coordinator.data.raw == OFF_FRAME
    assert client.writes == [bytes.fromhex("00 0e 0e")]
    assert client.calls[:4] == [
        ("start_notify", CHAR_COMMAND_NOTIFY),
        ("write", CHAR_WRITE, "00 0e 0e", True),
        ("stop_notify", CHAR_COMMAND_NOTIFY),
        ("start_notify", CHAR_STATUS_NOTIFY),
    ]
    assert coordinator.stats.drops == 1


async def test_silent_link_is_dropped_as_stale(hass) -> None:
    coordinator = await _coordinator(hass)
    got_data, client = await _run_session(hass, coordinator, frames=1, then_disconnect=False)

    assert got_data is True
    assert coordinator.stats.stale_timeouts == 1
    assert client.calls[-1] == ("disconnect",)

    coordinator._mark_unavailable()
    assert coordinator.available is False


async def test_missing_device_returns_without_connecting(hass) -> None:
    coordinator = await _coordinator(hass)
    with (
        patch(f"{MODULE}.bluetooth.async_ble_device_from_address", return_value=None),
        patch(f"{MODULE}.establish_connection") as establish,
    ):
        assert await coordinator._session() is False
    establish.assert_not_called()
