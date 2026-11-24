"""Diagnostics download with the heater's address removed."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import BougeRVHeaterConfigEntry

TO_REDACT = {CONF_ADDRESS, "address", "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BougeRVHeaterConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for one heater."""
    coordinator = entry.runtime_data
    status = coordinator.data
    payload: dict[str, Any] = {
        "entry": async_redact_data(
            {"title": entry.title, "unique_id": entry.unique_id, **entry.data}, TO_REDACT
        ),
        "firmware": coordinator.firmware,
        "available": coordinator.available,
        "connected": coordinator.connected,
        "stats": asdict(coordinator.stats),
        "status": None,
    }
    if status is not None:
        payload["status"] = {
            "raw": status.raw.hex(" "),
            "run_state": status.run_state.value,
            "fan": status.fan,
            "fuel_pump": status.fuel_pump,
            "glow_plug": status.glow_plug,
            "flame": status.flame,
            "intake": status.intake,
            "exhaust": status.exhaust,
            "fault_code": status.fault_code,
            "efficiency": status.efficiency,
            "actual_output_kw": status.actual_output_kw,
            "output_setting_kw": status.output_setting_kw,
            "voltage": status.voltage,
            "total_run_hours": status.total_run_hours,
            "schedule_enabled": status.schedule_enabled,
            "schedule_hours": list(status.schedule_hours),
            "unknown_fields": status.unknown_fields,
        }
    return payload
