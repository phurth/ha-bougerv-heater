"""Shared entity base."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import CommandError, HeaterCoordinator


def heater_device_info(coordinator: HeaterCoordinator) -> DeviceInfo:
    """Device registry entry for one heater."""
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.address)},
        connections={(dr.CONNECTION_BLUETOOTH, coordinator.address)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=entry.title if entry else MODEL,
        sw_version=coordinator.firmware,
    )


class HeaterEntity(CoordinatorEntity[HeaterCoordinator]):
    """An entity fed by the heater's status stream."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HeaterCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = heater_device_info(coordinator)

    @property
    def available(self) -> bool:
        """Unavailable whenever status is not arriving. Never report stale data."""
        return self.coordinator.available


# Refusals the user can fix by waiting or by changing what they asked for.
_VALIDATION_REASONS = {
    "fault_active",
    "cooling_down",
    "already_on",
    "already_off",
    "starting",
    "output_heater_off",
    "output_out_of_range",
}


async def run_command(command: Awaitable[None]) -> None:
    """Run a control command, turning refusals into messages Home Assistant shows."""
    try:
        await command
    except CommandError as err:
        error_cls = (
            ServiceValidationError if err.reason in _VALIDATION_REASONS else HomeAssistantError
        )
        raise error_cls(translation_domain=DOMAIN, translation_key=err.reason) from err
