"""Config flow: every path must end in an explicit MAC confirmation."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_bougerv_heater.const import CONF_MAC_CONFIRMED, DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

ADDRESS = "AA:BB:CC:DD:EE:01"
OTHER = "AA:BB:CC:DD:EE:02"


@pytest.fixture(autouse=True)
def _no_connection():
    """Create entries without starting a BLE connection."""
    with patch(
        "custom_components.ha_bougerv_heater.coordinator.HeaterCoordinator.async_start"
    ), patch("custom_components.ha_bougerv_heater.async_setup_entry", return_value=True):
        yield


def _info(name: str, address: str = ADDRESS, rssi: int = -70) -> BluetoothServiceInfoBleak:
    device = BLEDevice(address, name, {})
    advertisement = AdvertisementData(name, {0xFFFF: b"\x01"}, {}, [], None, rssi, ())
    return BluetoothServiceInfoBleak(
        name, address, rssi, {0xFFFF: b"\x01"}, {}, [], "local", device, advertisement, True, 0.0, None
    )


async def test_discovery_requires_mac_confirmation(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_info("BougeRV-DHG001")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"]["address"] == ADDRESS

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC_CONFIRMED: False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "confirm_mac"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MAC_CONFIRMED: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["title"] == "BougeRV Diesel Heater EE01"
    assert result["result"].unique_id == ADDRESS


async def test_pro_heater_is_not_offered(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_info("BougeRV-DHG005")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_already_configured_heater_is_aborted(hass) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={CONF_ADDRESS: ADDRESS}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=_info("BougeRV-DHG001")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_lists_heaters_strongest_first_and_requires_confirmation(hass) -> None:
    found = [
        _info("BougeRV-DHG001", ADDRESS, rssi=-90),
        _info("BougeRV-DHG002", OTHER, rssi=-60),
        _info("BougeRV-DHG10", "AA:BB:CC:DD:EE:03"),  # Pro, filtered out
        _info("SomethingElse", "AA:BB:CC:DD:EE:04"),
    ]
    with patch(
        "custom_components.ha_bougerv_heater.config_flow.async_discovered_service_info",
        return_value=found,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        schema = result["data_schema"].schema
        selector = next(value for key, value in schema.items() if key == CONF_ADDRESS)
        offered = [option["value"] for option in selector.config["options"]]
        assert offered == [OTHER, ADDRESS]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_MAC_CONFIRMED: False}
        )
        assert result["errors"] == {"base": "confirm_mac"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS, CONF_MAC_CONFIRMED: True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_user_step_without_heaters_aborts(hass) -> None:
    with patch(
        "custom_components.ha_bougerv_heater.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
