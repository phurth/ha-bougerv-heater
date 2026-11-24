"""Home Assistant thermostat for the heater. Disabled by default.

Only created when a temperature sensor is chosen in the integration options.
All control decisions are made by thermostat.ThermostatController; this
module gathers inputs, executes the chosen action, and persists state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_WHOLE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from . import BougeRVHeaterConfigEntry
from .const import (
    CONF_MIN_OFF_MINUTES,
    CONF_MIN_RUN_MINUTES,
    CONF_RESTART_BELOW,
    CONF_SENSOR_TIMEOUT_MINUTES,
    CONF_STOP_ABOVE,
    CONF_TEMPERATURE_SENSOR,
    DEFAULT_MIN_OFF_MINUTES,
    DEFAULT_MIN_RUN_MINUTES,
    DEFAULT_RESTART_BELOW,
    DEFAULT_SENSOR_TIMEOUT_MINUTES,
    DEFAULT_STOP_ABOVE,
    DOMAIN,
    THERMOSTAT_EVALUATE_INTERVAL,
)
from .coordinator import CommandError, HeaterCoordinator
from .entity import HeaterEntity
from .protocol import RunState
from .thermostat import Action, Settings, ThermostatController

_LOGGER = logging.getLogger(__name__)

_UNITS = {
    UnitOfTemperature.CELSIUS,
    UnitOfTemperature.FAHRENHEIT,
    UnitOfTemperature.KELVIN,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BougeRVHeaterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the thermostat when a temperature sensor has been chosen."""
    sensor = entry.options.get(CONF_TEMPERATURE_SENSOR)
    if not sensor:
        return
    options = entry.options
    settings = Settings(
        min_run=timedelta(minutes=options.get(CONF_MIN_RUN_MINUTES, DEFAULT_MIN_RUN_MINUTES)),
        min_off=timedelta(minutes=options.get(CONF_MIN_OFF_MINUTES, DEFAULT_MIN_OFF_MINUTES)),
        stop_above=options.get(CONF_STOP_ABOVE, DEFAULT_STOP_ABOVE),
        restart_below=options.get(CONF_RESTART_BELOW, DEFAULT_RESTART_BELOW),
    )
    timeout = timedelta(
        minutes=options.get(CONF_SENSOR_TIMEOUT_MINUTES, DEFAULT_SENSOR_TIMEOUT_MINUTES)
    )
    async_add_entities([HeaterThermostat(entry.runtime_data, sensor, settings, timeout)])


class ThermostatExtraData(ExtraStoredData):
    """Survives a restart: the target in °C, and the start and stop times.

    The target is stored here in °C because the state's ``temperature``
    attribute is in the display unit, which may be °F.
    """

    def __init__(
        self, target_c: float, last_start: datetime | None, last_stop: datetime | None
    ) -> None:
        self.target_c = target_c
        self.last_start = last_start
        self.last_stop = last_stop

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_c": self.target_c,
            "last_start": self.last_start.isoformat() if self.last_start else None,
            "last_stop": self.last_stop.isoformat() if self.last_stop else None,
        }


class HeaterThermostat(HeaterEntity, ClimateEntity, RestoreEntity):
    """Off/Heat thermostat driven by an external temperature sensor."""

    _attr_entity_registry_enabled_default = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = 5.0
    _attr_max_temp = 30.0
    # Whole degrees in whichever unit Home Assistant displays.
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1.0

    def __init__(
        self,
        coordinator: HeaterCoordinator,
        sensor: str,
        settings: Settings,
        sensor_timeout: timedelta,
    ) -> None:
        super().__init__(coordinator, "thermostat")
        self._sensor = sensor
        self._sensor_timeout = sensor_timeout
        self.controller = ThermostatController(settings=settings)
        self._room: float | None = None
        self._reason = "off"
        self._added_at = dt_util.utcnow()
        self._eval_lock = asyncio.Lock()
        self._eval_again = False
        self._issue_id = f"onboard_schedule_{coordinator.address}"

    # ------------------------------------------------------------ lifecycle

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._added_at = dt_util.utcnow()
        last = await self.async_get_last_state()
        extra = await self.async_get_last_extra_data()
        data = extra.as_dict() if extra is not None else {}
        if last is not None:
            self.controller.heating = last.state == HVACMode.HEAT
        target: float | None = None
        if data.get("target_c") is not None:
            target = float(data["target_c"])
        elif last is not None and (shown := last.attributes.get(ATTR_TEMPERATURE)) is not None:
            # Older saves only have the displayed value, in the system unit.
            target = TemperatureConverter.convert(
                float(shown), self.hass.config.units.temperature_unit, UnitOfTemperature.CELSIUS
            )
        if target is not None:
            self.controller.target = self._clamp(target)
        self.controller.last_start = dt_util.parse_datetime(data.get("last_start") or "")
        self.controller.last_stop = dt_util.parse_datetime(data.get("last_stop") or "")

        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._sensor], self._on_sensor_change)
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._on_tick, timedelta(seconds=THERMOSTAT_EVALUATE_INTERVAL)
            )
        )
        self.async_on_remove(self._delete_schedule_issue)
        self._update_from_status()
        self._schedule_evaluation()

    @property
    def extra_restore_state_data(self) -> ThermostatExtraData:
        return ThermostatExtraData(
            self.controller.target, self.controller.last_start, self.controller.last_stop
        )

    def _clamp(self, target_c: float) -> float:
        """Keep any target, however it arrived, inside the allowed range."""
        return min(max(target_c, self._attr_min_temp), self._attr_max_temp)

    # ------------------------------------------------------------ state

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.controller.heating else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        status = self.coordinator.data
        if status is None:
            return None
        if status.is_on:
            return HVACAction.HEATING
        if status.run_state is RunState.SHUTTING_DOWN:
            return HVACAction.FAN
        return HVACAction.IDLE if self.controller.heating else HVACAction.OFF

    @property
    def current_temperature(self) -> float | None:
        return self._room

    @property
    def target_temperature(self) -> float:
        return self.controller.target

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lockout = self.controller.lockout
        return {
            "control": self._reason,
            "lockout": lockout.value if lockout else None,
            "temperature_sensor": self._sensor,
        }

    # ------------------------------------------------------------ user input

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        status = self.coordinator.data if self.coordinator.available else None
        heating = hvac_mode == HVACMode.HEAT
        self.controller.set_heating(heating, heater_on=bool(status and status.is_on))
        _LOGGER.info("%s: thermostat %s", self.coordinator.address, "on" if heating else "off")
        self.async_write_ha_state()
        await self._async_evaluate()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self.controller.target = self._clamp(float(temperature))
        if (mode := kwargs.get("hvac_mode")) is not None:
            await self.async_set_hvac_mode(mode)
            return
        self.async_write_ha_state()
        await self._async_evaluate()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    # ------------------------------------------------------------ inputs

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_status()
        super()._handle_coordinator_update()
        self._schedule_evaluation()

    @callback
    def _update_from_status(self) -> None:
        status = self.coordinator.data if self.coordinator.available else None
        if status is None:
            self.controller.forget_heater()
            return
        if self.controller.observe(status, dt_util.utcnow()):
            _LOGGER.info(
                "%s: heater was stopped outside Home Assistant; thermostat turned off",
                self.coordinator.address,
            )
        self._sync_schedule_issue(status.schedule_enabled, status.schedule_hours)

    @callback
    def _on_sensor_change(self, _event: Event[EventStateChangedData]) -> None:
        self._schedule_evaluation()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._schedule_evaluation()

    def _read_room(self, now: datetime) -> tuple[float | None, bool]:
        """Return (temperature in °C or None, stale)."""
        state = self.hass.states.get(self._sensor)
        if state is None:
            return None, now - self._added_at > self._sensor_timeout
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            since = max(state.last_changed, self._added_at)
            return None, now - since > self._sensor_timeout
        try:
            value = float(state.state)
        except ValueError:
            return None, now - max(state.last_changed, self._added_at) > self._sensor_timeout
        unit = state.attributes.get("unit_of_measurement")
        if unit in _UNITS and unit != UnitOfTemperature.CELSIUS:
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        reported = max(getattr(state, "last_reported", state.last_updated), self._added_at)
        return round(value, 2), now - reported > self._sensor_timeout

    # ------------------------------------------------------------ control

    @callback
    def _schedule_evaluation(self) -> None:
        if self.hass is not None and self.enabled:
            self.hass.async_create_task(self._async_evaluate(), eager_start=False)

    async def _async_evaluate(self) -> None:
        """Run the controller; coalesce requests that arrive mid-evaluation."""
        if self._eval_lock.locked():
            self._eval_again = True
            return
        async with self._eval_lock:
            while True:
                self._eval_again = False
                await self._evaluate_once()
                if not self._eval_again:
                    break

    async def _evaluate_once(self) -> None:
        now = dt_util.utcnow()
        status = self.coordinator.data if self.coordinator.available else None
        room, stale = self._read_room(now)
        self._room = room
        decision = self.controller.decide(now, status, room, stale)
        self._reason = decision.reason
        address = self.coordinator.address

        if decision.action is not Action.NONE:
            _LOGGER.info("%s: thermostat %s (%s)", address, decision.action.value, decision.reason)
            try:
                if decision.action is Action.START:
                    await self.coordinator.async_set_power(True)
                    self.controller.started(now)
                elif decision.action is Action.STOP:
                    self.controller.stopping()
                    try:
                        await self.coordinator.async_set_power(False)
                    except CommandError:
                        self.controller.stop_failed()
                        raise
                    self.controller.stopped(now)
                elif decision.action is Action.SET_OUTPUT:
                    assert decision.output_kw is not None
                    self.controller.output_changed(now)
                    await self.coordinator.async_set_heat_output(decision.output_kw)
            except CommandError as err:
                if decision.action is Action.START and err.reason == "not_confirmed":
                    self.controller.start_failed()
                    self._reason = "locked out: start_failed"
                _LOGGER.warning(
                    "%s: thermostat %s failed: %s", address, decision.action.value, err.reason
                )
        self.async_write_ha_state()

    # ------------------------------------------------------------ repairs

    @callback
    def _sync_schedule_issue(self, enabled: bool, hours: tuple[int, ...]) -> None:
        if enabled and hours:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="onboard_schedule_conflict",
                translation_placeholders={
                    "name": self.coordinator.config_entry.title
                    if self.coordinator.config_entry
                    else "BougeRV heater",
                    "hours": ", ".join(f"{hour:02d}:00" for hour in hours),
                },
            )
        else:
            self._delete_schedule_issue()

    @callback
    def _delete_schedule_issue(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
