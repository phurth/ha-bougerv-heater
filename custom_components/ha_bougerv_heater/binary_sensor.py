"""Binary sensors for the heater's status stream."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BougeRVHeaterConfigEntry
from .coordinator import HeaterCoordinator
from .entity import HeaterEntity
from .protocol import HeaterStatus


def _schedule_attributes(status: HeaterStatus) -> dict[str, Any]:
    return {"hours": [f"{hour:02d}:00" for hour in status.schedule_hours]}


@dataclass(frozen=True, kw_only=True)
class HeaterBinarySensorDescription(BinarySensorEntityDescription):
    """A flag from one status frame."""

    value_fn: Callable[[HeaterStatus], bool]
    attributes_fn: Callable[[HeaterStatus], dict[str, Any]] | None = None


def _component(key: str) -> HeaterBinarySensorDescription:
    return HeaterBinarySensorDescription(
        key=key,
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: getattr(s, key),
    )


BINARY_SENSORS: tuple[HeaterBinarySensorDescription, ...] = (
    HeaterBinarySensorDescription(
        key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.fault_active,
    ),
    HeaterBinarySensorDescription(
        key="onboard_schedule",
        value_fn=lambda s: s.schedule_enabled,
        attributes_fn=_schedule_attributes,
    ),
    _component("fan"),
    _component("fuel_pump"),
    _component("glow_plug"),
    _component("flame"),
    _component("intake"),
    _component("exhaust"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BougeRVHeaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors for one heater."""
    coordinator = entry.runtime_data
    async_add_entities(
        HeaterBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class HeaterBinarySensor(HeaterEntity, BinarySensorEntity):
    """A flag from the latest status frame."""

    entity_description: HeaterBinarySensorDescription

    def __init__(
        self, coordinator: HeaterCoordinator, description: HeaterBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data
        return None if status is None else self.entity_description.value_fn(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self.coordinator.data
        if status is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(status)
