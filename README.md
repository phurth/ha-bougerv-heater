# BougeRV Diesel Heater — Home Assistant HACS Integration

Local Bluetooth monitoring and control for BougeRV portable diesel heaters (standard model) in Home Assistant, with no cloud account.

> **Disclaimer:** This is an unofficial community integration. It is not affiliated with, endorsed by, or supported by BougeRV. Use it at your own risk.

## Status

Version 0.2 adds **power and heat output controls**. They are **disabled by default**: enable them on the device page only if you want Home Assistant to be able to start the heater. A Home Assistant thermostat and onboard schedule editing are planned.

## Features / Entities

| Entity | Type | Notes |
|---|---|---|
| Run state | Sensor | Off, Starting, Running, Shutting down |
| Heat output | Sensor (kW) | Actual output |
| Heat output setting | Sensor (kW) | Selected output, 1.4–5.0 kW |
| Combustion efficiency | Sensor (%) | |
| Input voltage | Sensor (V) | Unknown while the heater is off, because the heater only measures it while running |
| Total run time | Sensor (h) | Lifetime hours |
| Fault | Binary sensor (problem) | On while the heater reports a fault |
| Fault code | Sensor | `none`, or a code such as `E3`, with the fault name, possible causes, and recovery steps as attributes |
| Onboard schedule | Binary sensor | On when the heater's built-in daily timer is enabled, with the selected hours as an attribute |
| Fan, Fuel pump, Glow plug, Flame, Intake, Exhaust | Binary sensors (diagnostic) | Component activity |
| Signal strength | Sensor (diagnostic, disabled by default) | Bluetooth RSSI |
| Status frame | Sensor (diagnostic, disabled by default) | Raw status bytes, for troubleshooting |
| Power | Switch (**disabled by default**) | On while starting or running. Turning it off starts the shutdown and cool-down |
| Heat output setting | Number (**disabled by default**) | 1.4–5.0 kW in 0.1 kW steps. Can be changed only while the heater is starting or running |

The heater sends status about once a second over a held-open Bluetooth connection. If status stops arriving, every entity becomes **unavailable** rather than showing stale values. A heater that has lost its connection may still be running.

## Requirements

- Home Assistant 2025.2 or newer with Bluetooth set up, using a local adapter or an ESPHome Bluetooth proxy.
- A BougeRV standard diesel heater that advertises as `BougeRV-DHG001`–`DHG004` or `LFB00000`. The Diesel Heater Pro (`BougeRV-DHG005`, `BougeRV-DHG10`) is **not** supported.

The heater accepts more than one Bluetooth connection at a time, so the BougeRV phone app keeps working alongside Home Assistant.

## Installation (HACS)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/phurth/ha-bougerv-heater` with type **Integration**.
3. Install **BougeRV Diesel Heater**, then restart Home Assistant.

## Configuration

Home Assistant discovers heaters in Bluetooth range automatically. You can also add one from **Settings → Devices & services → Add integration → BougeRV Diesel Heater**.

**Confirm the MAC address before adding a heater.** The heater has no Bluetooth pairing or password, so any BougeRV heater in range can be added, including a neighbor's. Setup shows the heater's MAC address and will not finish until you confirm it:

1. In the BougeRV app, open the heater and tap the settings (gear) icon.
2. Check that the MAC address shown there matches the one in Home Assistant.

If the app doesn't show a MAC address, unplug your heater for a minute and start discovery again. Your heater will not be listed while it is unplugged.

## Controls and safety

The controls follow the same rules as the BougeRV app, and Home Assistant refuses a command with an explanation when it isn't allowed:

- **Power on** only from Off with no active fault. It is refused during the cool-down.
- **Power off** while running, or while the heater is on with an active fault. It is refused during a normal startup, as in the app, and is unnecessary during the cool-down.
- **Heat output** only while starting or running with no active fault.

Every command is sent once, and the integration then waits for the heater's status to confirm it. If the heater doesn't confirm within a few seconds, Home Assistant shows an error rather than assuming the command worked.

Remote starting a combustion heater carries real risk. Before automating the power switch:

- Keep a working carbon monoxide alarm near the heater.
- Never cut the heater's battery power until it shows Off after the cool-down. Removing power early can damage the heater.
- If the Bluetooth link drops while the heater is running, Home Assistant can't turn it off. The heater keeps running until it's stopped from the app or its own controls.

## Notes & Limitations

- **No authentication.** The heater's Bluetooth protocol has no pairing or password. Any Bluetooth device in range can talk to it. This integration cannot change that.
- **Onboard schedule.** The heater can start itself from its built-in timer without any app or Home Assistant connection. The Onboard schedule entity shows whether that timer is enabled.
- **Weak signal.** At around −80 dBm the connection may drop occasionally. The integration reconnects automatically, but a Bluetooth proxy near the heater gives the most reliable results.
- Protocol details are in [docs/TECH_SPEC.md](docs/TECH_SPEC.md).

## License

MIT
