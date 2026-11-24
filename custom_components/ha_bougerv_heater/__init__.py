"""BougeRV diesel heater integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .coordinator import HeaterCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type BougeRVHeaterConfigEntry = ConfigEntry[HeaterCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BougeRVHeaterConfigEntry) -> bool:
    """Set up one heater.

    Setup succeeds even when the heater is out of range or unplugged. Its
    entities stay unavailable until status frames arrive.
    """
    coordinator = HeaterCoordinator(hass, entry, entry.data[CONF_ADDRESS])
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BougeRVHeaterConfigEntry) -> None:
    """Reload so the thermostat picks up its new settings."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BougeRVHeaterConfigEntry) -> bool:
    """Unload a heater and drop its connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
