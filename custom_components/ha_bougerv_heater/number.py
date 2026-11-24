"""Heat output setting. Disabled by default."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BougeRVHeaterConfigEntry
from .entity import HeaterEntity, run_command
from .protocol import OUTPUT_MAX_RAW, OUTPUT_MIN_RAW


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BougeRVHeaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the heat output control for one heater."""
    async_add_entities([HeaterOutputNumber(entry.runtime_data, "heat_output_control")])


class HeaterOutputNumber(HeaterEntity, NumberEntity):
    """1.4-5.0 kW in 0.1 kW steps. Shows the setting even while the heater is off."""

    _attr_entity_registry_enabled_default = False
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_native_min_value = OUTPUT_MIN_RAW / 10
    _attr_native_max_value = OUTPUT_MAX_RAW / 10
    _attr_native_step = 0.1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> float | None:
        status = self.coordinator.data
        return None if status is None else status.output_setting_kw

    async def async_set_native_value(self, value: float) -> None:
        await run_command(self.coordinator.async_set_heat_output(value))
