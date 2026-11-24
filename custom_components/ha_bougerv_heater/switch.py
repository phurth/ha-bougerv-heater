"""Power switch. Disabled by default: it starts a combustion heater."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BougeRVHeaterConfigEntry
from .entity import HeaterEntity, run_command


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BougeRVHeaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the power switch for one heater."""
    async_add_entities([HeaterPowerSwitch(entry.runtime_data, "power")])


class HeaterPowerSwitch(HeaterEntity, SwitchEntity):
    """On while starting or running; off while off or cooling down."""

    _attr_entity_registry_enabled_default = False

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data
        return None if status is None else status.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        await run_command(self.coordinator.async_set_power(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await run_command(self.coordinator.async_set_power(False))
