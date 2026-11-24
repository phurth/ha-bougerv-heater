"""Held-open BLE connection to one heater, pushed into a coordinator."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bleak import BleakClient
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CHAR_COMMAND_NOTIFY,
    CHAR_STATUS_NOTIFY,
    CHAR_WRITE,
    DOMAIN,
    FIRMWARE_REPLY_TIMEOUT,
    FIRMWARE_RETRY_DELAY,
    NOT_IN_RANGE_RECHECK,
    NOTIFY_SWITCH_DELAY,
    RECONNECT_AFTER_DROP,
    RECONNECT_MAX,
    RECONNECT_MIN,
    STALE_AFTER,
)
from .protocol import HeaterStatus, firmware_query, parse_firmware, parse_status

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass
class ConnectionStats:
    """Counters for the diagnostics download."""

    sessions: int = 0
    drops: int = 0
    stale_timeouts: int = 0
    bad_frames: int = 0
    last_error: str | None = None


class HeaterCoordinator(DataUpdateCoordinator[HeaterStatus | None]):
    """Keeps one connection open and publishes every changed status frame."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {address}",
        )
        self.address = address
        self.firmware: str | None = None
        self.stats = ConnectionStats()
        self._fresh = False
        self._last_frame_at: float | None = None
        self._last_raw: bytes | None = None
        self._firmware_event = asyncio.Event()
        self._wake = asyncio.Event()
        self._client: BleakClient | None = None
        self._cancel_advert: CALLBACK_TYPE | None = None
        self._was_available = False

    # ------------------------------------------------------------------ state

    @property
    def available(self) -> bool:
        """True only while fresh status is arriving over a live connection."""
        return self._fresh and self.data is not None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @callback
    def _mark_unavailable(self) -> None:
        if self._fresh:
            self._fresh = False
            self.async_update_listeners()
        if self._was_available:
            self._was_available = False
            _LOGGER.info("%s: heater is unavailable", self.address)

    # ------------------------------------------------------------- lifecycle

    @callback
    def async_start(self) -> None:
        """Start the connection loop and listen for advertisements."""
        entry = self.config_entry
        assert entry is not None
        self._cancel_advert = bluetooth.async_register_callback(
            self.hass,
            self._on_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        entry.async_create_background_task(
            self.hass, self._run(), f"{DOMAIN} connection {self.address}"
        )

    async def async_stop(self) -> None:
        """Stop listening and drop the connection."""
        if self._cancel_advert is not None:
            self._cancel_advert()
            self._cancel_advert = None
        client = self._client
        self._client = None
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - best effort on shutdown
                _LOGGER.debug("%s: error while disconnecting", self.address, exc_info=True)
        self._mark_unavailable()

    @callback
    def _on_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self._wake.set()

    # ---------------------------------------------------------------- loop

    async def _run(self) -> None:
        failures = 0
        while True:
            if not bluetooth.async_address_present(self.hass, self.address, connectable=True):
                _LOGGER.debug("%s: not in range, waiting for an advertisement", self.address)
                self._wake.clear()
                try:
                    async with asyncio.timeout(NOT_IN_RANGE_RECHECK):
                        await self._wake.wait()
                except TimeoutError:
                    continue

            got_data = False
            try:
                got_data = await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                self.stats.last_error = f"{type(err).__name__}: {err}"
                _LOGGER.debug("%s: session failed: %s", self.address, err)
            self._mark_unavailable()

            if got_data:
                failures = 0
                delay = RECONNECT_AFTER_DROP
            else:
                failures += 1
                delay = min(RECONNECT_MIN * 2 ** (failures - 1), RECONNECT_MAX)
            await asyncio.sleep(delay)

    async def _session(self) -> bool:
        """Run one connection until it drops or goes stale. True if data arrived."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            return False

        disconnected = asyncio.Event()

        def _on_disconnect(_client: BleakClient) -> None:
            disconnected.set()

        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.config_entry.title if self.config_entry else self.address,
            disconnected_callback=_on_disconnect,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            or ble_device,
            max_attempts=3,
        )
        self._client = client
        self.stats.sessions += 1
        self._last_frame_at = None
        got_data = False
        try:
            await self._read_firmware(client)
            await asyncio.sleep(NOTIFY_SWITCH_DELAY)
            await client.start_notify(CHAR_STATUS_NOTIFY, self._on_status)
            started = time.monotonic()

            while not disconnected.is_set():
                try:
                    async with asyncio.timeout(1.0):
                        await disconnected.wait()
                except TimeoutError:
                    pass
                if self._last_frame_at is not None:
                    got_data = True
                last = self._last_frame_at or started
                if time.monotonic() - last > STALE_AFTER:
                    self.stats.stale_timeouts += 1
                    _LOGGER.debug("%s: no status for %.0f s", self.address, STALE_AFTER)
                    break
            if disconnected.is_set():
                self.stats.drops += 1
        finally:
            self._client = None
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("%s: disconnect failed", self.address, exc_info=True)
        return got_data

    async def _read_firmware(self, client: BleakClient) -> None:
        """Query the firmware version. Failure here does not end the session."""
        self._firmware_event.clear()
        try:
            await client.start_notify(CHAR_COMMAND_NOTIFY, self._on_command_reply)
            await client.write_gatt_char(CHAR_WRITE, firmware_query(), response=True)
            await asyncio.sleep(FIRMWARE_RETRY_DELAY)
            if not self._firmware_event.is_set():
                await client.write_gatt_char(CHAR_WRITE, firmware_query(), response=True)
            async with asyncio.timeout(FIRMWARE_REPLY_TIMEOUT):
                await self._firmware_event.wait()
        except TimeoutError:
            _LOGGER.debug("%s: no firmware reply", self.address)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s: firmware query failed: %s", self.address, err)
        finally:
            try:
                await client.stop_notify(CHAR_COMMAND_NOTIFY)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------- notifications

    @callback
    def _on_command_reply(self, _char: object, data: bytearray) -> None:
        firmware = parse_firmware(bytes(data))
        if firmware is None:
            return
        self._firmware_event.set()
        if firmware != self.firmware:
            self.firmware = firmware
            self._update_device_firmware()

    @callback
    def _update_device_firmware(self) -> None:
        if self.config_entry is None:
            return
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(registry, self.config_entry.entry_id):
            if (DOMAIN, self.address) in device.identifiers and device.sw_version != self.firmware:
                registry.async_update_device(device.id, sw_version=self.firmware)

    @callback
    def _on_status(self, _char: object, data: bytearray) -> None:
        self.handle_status_frame(bytes(data))

    @callback
    def handle_status_frame(self, frame: bytes) -> None:
        """Publish a status frame if it is valid and new, or if we were stale."""
        status = parse_status(frame)
        if status is None:
            self.stats.bad_frames += 1
            return
        self._last_frame_at = time.monotonic()
        if not self._was_available:
            self._was_available = True
            _LOGGER.info("%s: receiving heater status", self.address)
        if self._fresh and frame == self._last_raw:
            return
        self._fresh = True
        self._last_raw = frame
        self.async_set_updated_data(status)
