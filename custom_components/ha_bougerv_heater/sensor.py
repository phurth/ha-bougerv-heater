"""Sensors for the heater's status stream."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfPower,
    UnitOfElectricPotential,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BougeRVHeaterConfigEntry
from .coordinator import HeaterCoordinator
from .entity import HeaterEntity, heater_device_info
from .faults import describe_fault
from .protocol import HeaterStatus, RunState

NO_FAULT_STATE = "none"


def _voltage(status: HeaterStatus) -> float | None:
    # The heater only measures input voltage while it is running. It reports
    # 0.0 V while off, which would be misleading as a reading.
    if status.run_state is RunState.OFF and status.voltage == 0:
        return None
    return status.voltage


def _fault_state(status: HeaterStatus) -> str:
    if status.fault_code is None:
        return NO_FAULT_STATE
    return describe_fault(status.fault_code).label


def _fault_attributes(status: HeaterStatus) -> dict[str, Any]:
    if status.fault_code is None:
        return {}
    fault = describe_fault(status.fault_code)
    return {
        "raw_code": fault.code,
        "fault": fault.name,
        "possible_causes": list(fault.causes),
        "recovery_steps": list(fault.recovery),
    }


@dataclass(frozen=True, kw_only=True)
class HeaterSensorDescription(SensorEntityDescription):
    """A sensor computed from one status frame."""

    value_fn: Callable[[HeaterStatus], Any]
    attributes_fn: Callable[[HeaterStatus], dict[str, Any]] | None = None


SENSORS: tuple[HeaterSensorDescription, ...] = (
    HeaterSensorDescription(
        key="run_state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in RunState],
        value_fn=lambda s: s.run_state.value,
    ),
    HeaterSensorDescription(
        key="heat_output",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.actual_output_kw,
    ),
    HeaterSensorDescription(
        key="heat_output_setting",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=1,
        value_fn=lambda s: s.output_setting_kw,
    ),
    HeaterSensorDescription(
        key="combustion_efficiency",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.efficiency,
    ),
    HeaterSensorDescription(
        key="input_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_voltage,
    ),
    HeaterSensorDescription(
        key="total_run_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: s.total_run_hours,
    ),
    HeaterSensorDescription(
        key="fault_code",
        value_fn=_fault_state,
        attributes_fn=_fault_attributes,
    ),
    HeaterSensorDescription(
        key="status_frame",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.raw.hex(" "),
        attributes_fn=lambda s: s.unknown_fields,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BougeRVHeaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for one heater."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        HeaterSensor(coordinator, description) for description in SENSORS
    ]
    entities.append(HeaterSignalStrengthSensor(coordinator))
    async_add_entities(entities)


class HeaterSensor(HeaterEntity, SensorEntity):
    """A value from the latest status frame."""

    entity_description: HeaterSensorDescription

    def __init__(
        self, coordinator: HeaterCoordinator, description: HeaterSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        status = self.coordinator.data
        return None if status is None else self.entity_description.value_fn(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        status = self.coordinator.data
        if status is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(status)


class HeaterSignalStrengthSensor(SensorEntity):
    """Strongest recent advertisement RSSI, polled from the Bluetooth manager."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_should_poll = True

    def __init__(self, coordinator: HeaterCoordinator) -> None:
        self._address = coordinator.address
        self._attr_unique_id = f"{coordinator.address}_signal_strength"
        self._attr_device_info = heater_device_info(coordinator)

    async def async_update(self) -> None:
        info = bluetooth.async_last_service_info(self.hass, self._address, connectable=False)
        self._attr_available = info is not None
        self._attr_native_value = info.rssi if info is not None else None


SCAN_INTERVAL = timedelta(seconds=60)
