# BougeRV Diesel Heater — Home Assistant HACS Integration

Local Bluetooth monitoring and control for BougeRV portable diesel heaters (standard model) in Home Assistant, with no cloud account.

> **Disclaimer:** This is an unofficial community integration. It is not affiliated with, endorsed by, or supported by BougeRV. Use it at your own risk.

## Status

Version 0.3 adds a **thermostat** driven by any Home Assistant temperature sensor, alongside the **power and heat output controls** from 0.2. All controls are **disabled by default**: enable them on the device page only if you want Home Assistant to be able to start the heater.

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
| Onboard schedule | Binary sensor | On when the heater's built-in daily timer is enabled, with the selected hours (as the app labels them) as an attribute |
| Fan, Fuel pump, Glow plug, Flame, Intake, Exhaust | Binary sensors (diagnostic) | Component activity |
| Signal strength | Sensor (diagnostic, disabled by default) | Bluetooth RSSI |
| Status frame | Sensor (diagnostic, disabled by default) | Raw status bytes, for troubleshooting |
| Power | Switch (**disabled by default**) | On while starting or running. Turning it off starts the shutdown and cool-down |
| Heat output setting | Number (**disabled by default**) | 1.4–5.0 kW in 0.1 kW steps. Can be changed only while the heater is starting or running |
| Thermostat | Climate (**disabled by default**) | Off / Heat with a target temperature. Created once a temperature sensor is chosen in the options |

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

## Thermostat

1. Open **Settings → Devices & services → BougeRV Diesel Heater → Configure** and choose a temperature sensor in the space being heated.
2. Enable the **Thermostat** entity on the device page.
3. Set it to **Heat** with a target temperature.

The thermostat adjusts heat output first: full output when the room is well below target, easing to 1.4 kW as it gets close. It stops the heater only if the room stays above the target by the stop margin (2 °C by default) at minimum output for 3 minutes, so a brief spike from a jumpy sensor doesn't cause a stop. It restarts once it falls below the target by the restart margin (1 °C). Minimum run and off times (15 and 10 minutes by default) prevent short cycling. All of these are adjustable in the options.

Safety behavior:

- **Turning the thermostat off** shuts the heater down normally, including the cool-down.
- **Stopping the heater from the app or its panel** switches the thermostat off, so it won't restart a heater someone stopped on purpose.
- **If the temperature sensor stops reporting** for the sensor timeout (15 minutes by default), a running heater is shut down.
- **A heater fault, or a start the heater doesn't confirm,** locks the thermostat out until you turn it off and on again.
- **The heater's onboard schedule** can start it even while the thermostat is off. If the schedule is on with hours selected, Home Assistant shows a repair notice. Clear the hours in the BougeRV app if you use the thermostat.
- The thermostat runs in Home Assistant. If Home Assistant or the Bluetooth link fails while the heater is running, the heater keeps running at its last output.

## Notes & Limitations

- **No authentication.** The heater's Bluetooth protocol has no pairing or password. Any Bluetooth device in range can talk to it. This integration cannot change that.
- **Onboard schedule.** The heater can start itself from its built-in timer without any app or Home Assistant connection. The Onboard schedule entity shows whether that timer is enabled and which hours are set. Editing the schedule is not supported: use automations or the thermostat instead.
- **Weak signal.** At around −80 dBm the connection may drop occasionally. The integration reconnects automatically, but a Bluetooth proxy near the heater gives the most reliable results.
- Protocol details are in [docs/TECH_SPEC.md](docs/TECH_SPEC.md).

## License

MIT
