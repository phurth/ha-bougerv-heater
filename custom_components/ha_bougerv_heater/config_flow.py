"""Config flow: discover a heater and make the user confirm its MAC address.

The heater has no pairing, PIN, or password, so any heater within Bluetooth
range can be added. The MAC address is the only thing that ties an entry to
the user's own heater, so every path ends in an explicit MAC confirmation.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_MAC_CONFIRMED, DOMAIN, TITLE, is_standard_heater_name


def _entry_title(address: str) -> str:
    return f"{TITLE} {address.replace(':', '')[-4:]}"


def _placeholders(info: BluetoothServiceInfoBleak) -> dict[str, str]:
    return {"name": info.name or "BougeRV heater", "address": info.address, "rssi": str(info.rssi)}


class BougeRVHeaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one BougeRV standard diesel heater."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._candidates: dict[str, BluetoothServiceInfoBleak] = {}

    def _create(self, address: str) -> ConfigFlowResult:
        return self.async_create_entry(title=_entry_title(address), data={CONF_ADDRESS: address})

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a heater found by Bluetooth discovery."""
        if not is_standard_heater_name(discovery_info.name):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name,
            "address": discovery_info.address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the discovered MAC and require the user to confirm it."""
        assert self._discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_MAC_CONFIRMED):
                return self._create(self._discovery.address)
            errors["base"] = "confirm_mac"
        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_MAC_CONFIRMED, default=False): bool}),
            description_placeholders=_placeholders(self._discovery),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick from heaters in range, strongest signal first, and confirm the MAC."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if not user_input.get(CONF_MAC_CONFIRMED):
                errors["base"] = "confirm_mac"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self._create(address)

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if (
                info.address not in configured
                and info.address not in self._candidates
                and is_standard_heater_name(info.name)
            ):
                self._candidates[info.address] = info
        if not self._candidates:
            return self.async_abort(reason="no_devices_found")

        ordered = sorted(self._candidates.values(), key=lambda info: info.rssi, reverse=True)
        options = [
            SelectOptionDict(
                value=info.address,
                label=f"{info.name} — {info.address} ({info.rssi} dBm)",
            )
            for info in ordered
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS, default=ordered[0].address): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                vol.Required(CONF_MAC_CONFIRMED, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
