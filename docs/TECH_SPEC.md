# BougeRV Diesel Heater — Technical Specification

Technical reference for the planned `ha_bougerv_heater` Home Assistant
integration: the Bluetooth Low Energy protocol used by the BougeRV portable
diesel heater, the fields it reports, the commands it accepts, and the proposed
integration design.

This document covers the **standard (non-Pro) heater**. The Diesel Heater Pro
shares the same GATT service but has a larger command set and is out of scope.

> **Validation status (2026-11-24):** the read path has been confirmed on a
> `BougeRV-DHG001` heater with firmware V2.1.2, in the Off state, over a local
> BlueZ adapter: discovery, GATT profile, firmware query, status stream, and
> clock sync. **No control command has been tested**, and nothing has been
> observed while the heater is running. Remaining items are in
> [section 10](#10-hardware-validation-checklist).

---

## 1. Device Identification

| Model family | Advertised name prefix |
|---|---|
| **Standard heater (in scope)** | `BougeRV-DHG001`, `BougeRV-DHG002`, `BougeRV-DHG003`, `BougeRV-DHG004`, `LFB00000` |
| Diesel Heater Pro (out of scope) | `BougeRV-DHG005`, `BougeRV-DHG10` |

Both families use the same service and characteristic UUIDs, so **the advertised
name is what distinguishes them**. A wildcard such as `BougeRV-DHG00*` also
matches the Pro's `DHG005` prefix, so discovery must apply the exact prefix list
above.

### 1.1 Advertisement (observed)

| Field | Value |
|---|---|
| Local name | `BougeRV-DHG001` |
| Address type | Public |
| Service UUIDs | **None advertised** |
| Manufacturer data | Company ID `0xFFFF`, payload = the 6-byte device address followed by `01 01 01` |

Because no service UUID is advertised and `0xFFFF` is a generic test ID,
discovery must match on the local name.

---

## 2. GATT Profile

| Role | UUID | Use |
|---|---|---|
| Service | `43f4b114-ca67-48e8-a46f-9a8ffeb7146a` | Heater control service |
| Write | `bf83f3f1-399a-414d-9035-ce64ceb3ff67` | All commands |
| Command notify | `bf83f3f2-399a-414d-9035-ce64ceb3ff67` | Firmware version reply |
| Status notify | `bf83f3f3-399a-414d-9035-ce64ceb3ff67` | Status stream |

There is no application-layer login, session key, or payload encryption.
**No pairing or link encryption is required** (observed): notifications enable
and writes succeed on an unbonded connection.

Observed properties:

| Characteristic | Properties | Descriptors |
|---|---|---|
| `…f3f1` | write, write-without-response | `2901` |
| `…f3f2` | notify | `2901`, `2902` |
| `…f3f3` | notify | `2901`, `2902` |

Writes with response work. Every command is 9 bytes or fewer, so the default
23-byte ATT MTU is sufficient, and no MTU exchange is needed.

The heater accepts **more than one simultaneous central** (observed with two
phone clients at once), so a held-open connection does not lock out the phone
app.

---

## 3. Command Framing

Commands are written to `bf83f3f1-…`:

```text
00 <command> [payload...] <checksum>
```

The checksum is the low 8 bits of the sum of **all preceding bytes**:

```python
def checksum(frame: bytes) -> int:
    return sum(frame) & 0xFF
```

Example, setting output to 3.0 kW:

```text
00 07 1E 25
         └─ 0x00 + 0x07 + 0x1E = 0x25
```

---

## 4. Connection Sequence

1. Connect. A 512-byte MTU request is optional (see section 2).
2. Subscribe to the command notify characteristic (`…f3f2`).
3. Write the firmware query `00 0E 0E`, then repeat it after 300 ms.
4. Parse the first `…f3f2` notification as the firmware reply, then unsubscribe.
5. Wait 400 ms.
6. Subscribe to the status notify characteristic (`…f3f3`).
7. Write the current local time (command `0x0A`). Retry once after 300 ms if the
   write fails.
8. Consume status frames from `…f3f3`.

A failed subscription is retried after 200 ms. If no status frame arrives within
10 seconds of starting the session, treat the connection as failed and disconnect.

**Observed behavior:**

- The firmware reply arrives within about 0.8 s of the first query, so the
  second query is unnecessary when a reply has already arrived.
- Status frames flow **without** the clock write. The clock write is only needed
  to keep the onboard schedule accurate.
- Status frames arrive about **once per second** (0.7–1.3 s apart), including
  while the heater is off.
- The link stayed up for at least 78 s with no periodic writes, so no keep-alive
  is needed.
- One connection attempt and one early session dropped at about −78 dBm. The
  integration should reconnect automatically.
- When a client process exits without disconnecting, BlueZ keeps the link open.
  Always disconnect explicitly.

---

## 5. Commands

| Cmd | Name | Frame | Values |
|---|---|---|---|
| `0x05` | Power | `00 05 VV CS` | `00` off, `01` on |
| `0x07` | Heat output | `00 07 VV CS` | `0x0E`–`0x32` (14–50), in 0.1 kW |
| `0x0A` | Set clock | `00 0A YY MM DD hh mm ss CS` | Two-digit year, local time |
| `0x0B` | Set schedule hours | `00 0B 00 H0 H1 H2 00 CS` | See [section 8](#8-onboard-schedule) |
| `0x0E` | Firmware query | `00 0E CS` | — |
| `0x0F` | Schedule enable | `00 0F VV CS` | `00` off, `01` on |

Command `0x06` (heating mode) exists in the shared command set but is **not used
by the standard heater and must not be sent**. Commands `0x08` and above that are
not listed here belong to the Pro.

**Reference frames:**

| Action | Bytes |
|---|---|
| Power on | `00 05 01 06` |
| Power off | `00 05 00 05` |
| Output 1.4 kW (minimum) | `00 07 0E 15` |
| Output 3.0 kW | `00 07 1E 25` |
| Output 5.0 kW (maximum) | `00 07 32 39` |
| Firmware query | `00 0E 0E` |
| Clock, 2026-11-23 19:45:30 | `00 0A 1A 0B 17 13 2D 1E A4` |
| Schedule 01:00 and 05:00 (app labels) | `00 0B 00 11 00 00 00 1C` |
| Schedule enable / disable | `00 0F 01 10` / `00 0F 00 0F` |

---

## 6. Notification Frames

Notifications from the heater use this layout:

```text
<header> <frame id> [payload...] <checksum>
```

The checksum uses the same sum rule as commands, covering every preceding byte,
and it was valid on every frame observed. Frames that fail the checksum should
be discarded.

| Header | Frame | Length (observed) |
|---|---|---|
| `0x80` | Status (`0x41`) on `…f3f3` | 20 bytes |
| `0x40` | Firmware reply (`0x0E`) on `…f3f2` | 9 bytes |

### 6.1 Status frame

| Byte | Field | Type | Decoding |
|---|---|---|---|
| 0 | Header | u8 | `0x80` |
| 1 | Frame ID | u8 | `0x41` |
| 2 | State and component flags | bitfield | [Section 6.2](#62-state-and-component-flags-byte-2) |
| 3 | Fault code | u8 | `0xFF` means no fault; see [section 7](#7-fault-codes) |
| 4 | Combustion efficiency | u8 | Percent, clamp to 0–99 |
| 5 | Actual output | s8 | × 0.1 kW |
| 6 | Unknown | u8 | Not room temperature (see below) |
| 7–8 | Not interpreted | — | Unverified |
| 9–10 | Total run time | u16 LE | Hours |
| 11 | Mode flags | bitfield | Bit 0 unknown, bit 1 schedule enabled (confirmed) |
| 12 | Output setpoint | s8 | × 0.1 kW |
| 13 | Temperature setpoint | s8 | Not used by the standard heater |
| 14 | Input voltage | u8 | × 0.2 V |
| 15 | Schedule slots 01:00–08:00 | bitmap | Bit *n* = slot labelled (*n* + 1):00 |
| 16 | Schedule slots 09:00–16:00 | bitmap | Bit *n* = slot labelled (*n* + 9):00 |
| 17 | Schedule slots 17:00–24:00 | bitmap | Bit *n* = slot labelled (*n* + 17):00; bit 7 = 24:00 (midnight) |
| 18 | Unknown | — | `0x00` in every frame observed |
| 19 | Checksum | u8 | Sum of bytes 0–18 |

**Observed frame, heater off and idle:**

```text
80 41 00 FF 00 00 3D 00 00 00 00 02 32 18 00 00 00 00 00 49
```

This decodes as Off, all components off, no fault, 0% efficiency, 0.0 kW actual
output, room byte 61, 0 run hours, byte 11 = `0b10`, output setpoint 5.0 kW,
temperature byte 24, **voltage 0.0 V**, and no schedule hours.

Open questions from this frame:

- **Voltage reads 0.0 V while off**, which the phone app also shows, so it is
  only measured while running. The voltage sensor reports unknown while off.
- **Byte 6 is not room temperature.** It read 61 while the heater sat in
  ~30 °F (−1 °C) ambient overnight, then 62 later the same morning, so it tracks
  something that changes slowly. Its meaning is unknown, so it is not exposed.
- **Byte 11 bit 1 is the schedule enable flag.** It is set while the schedule
  toggle is on with no hours selected, as the phone app confirms.

### 6.2 State and component flags (byte 2)

| Bits | Field |
|---|---|
| 0–1 | Run state |
| 2 | Fan |
| 3 | Fuel pump |
| 4 | Ignition / glow plug |
| 5 | Flame |
| 6 | Intake |
| 7 | Exhaust |

| Bits 1–0 | Run state |
|---|---|
| `00` | Off |
| `01` | On: **Running** if flame = 1 and ignition = 0, otherwise **Starting** |
| `10` | Shutting down |
| `11` | Starting |

### 6.2a Observed run cycle (2026-11-24, cold unit outdoors, setting 5.0 kW)

| Time | Elapsed | Byte 2 | Event |
|---|---|---|---|
| 10:38:17 | 0:00 | `0x03` | Start: state `11` (Starting), nothing active |
| 10:38:21 | 0:04 | `0x01` | State `01`; voltage now reported (13.0 V) |
| 10:38:25 | 0:08 | `0x45` | Fan and intake on |
| 10:38:29 | 0:12 | `0x55` | Glow plug on; voltage sags to ~12.2 V |
| 10:39:59 | 1:42 | `0x5D` | Fuel pump on after 90 s of preheat |
| 10:40:31 | 2:14 | `0xFD` | Flame and exhaust on |
| 10:42:43 | 4:26 | `0xED` | Glow plug off: **Running**; voltage back to 13.0 V |
| ~10:49 | ~11:00 | `0xED` | Output levels at 4.8 kW (setting 5.0) |
| 10:51:56 | 0:00 | `0xB6` | Stop: state `10` (Shutting down); fuel pump and intake off, **glow plug back on**, flame still set |
| 10:52:34 | 0:38 | `0x96` | Flame off |
| 10:52:48 | 0:52 | `0x86` | Glow plug off; fan and exhaust remain |
| 10:56:27 | 4:31 | `0x82` | Fan off; exhaust flag still set |
| 10:56:29 | 4:33 | `0x00` | **Off**; voltage back to 0.0 V |

Findings:

- The run-state rule in 6.2 matched every transition, including Starting
  while the flame is lit but the glow plug is still on.
- **The glow plug runs again for about 50 s at shutdown**, which looks like a
  burn-off of residual fuel. A glow plug that is on does not imply startup.
- **The exhaust flag outlasts the flame by about 4 minutes**, so it likely
  indicates a hot exhaust rather than combustion.
- Output ramps about 0.5 kW per minute with one brief dip, and levelled at
  4.8 kW against a 5.0 kW setting.
- **Byte 4 reaches 97–125 while running.** The 0–99 clamp hides that range, so
  "99%" means "at least 99". Byte 4 is probably not a true percentage.
- Voltage is 12.8–13.2 V while running and 0 V while off.
- **Byte 6** rose steadily from 64 to 73 over the 18 minutes, including after
  the flame went out, and never jumped. It looks like a slowly warming internal
  temperature with an unknown unit.
- Bytes 0, 7, 8, 11, 13, and 15–18 did not change. Bytes 9–10 stayed at 0 run
  hours over this 14-minute run.

### 6.2b Observed run controlled from Home Assistant (2026-11-24)

| Time | Event |
|---|---|
| 11:16:00 | Power on (`00 05 01 06`) confirmed: Starting |
| 11:20:28 | Running, 4:28 after start (app start: 4:26) |
| 11:22:09 | Link lost on the local adapter (about −96 dBm) while running |
| 11:22:13 | Reconnected through an ESPHome proxy; **heater kept running** at 3.5 kW |
| 11:22:49 | Heat output 3.0 kW (`00 07 1E 25`) confirmed: byte 12 = 30 |
| ~11:24 | Actual output down from 3.6 to 2.7 kW, following the new setting |
| 11:24:35 | Power off (`00 05 00 05`) confirmed: Shutting down |
| 11:25:45 | Status stopped with the link still up; dropped as stale after 10 s, reconnected at 11:25:51 |
| 11:29:07 | Off, 4:32 after power off (app shutdown: 4:33) |

Commands from Home Assistant produced the same sequences and timings as the
phone app. Power off during startup and heat output changes while off were
both refused with their messages, and nothing was sent. The heat output setting persists through shutdown. A dropped link
does not stop a running heater.

### 6.2c Observed thermostat runs (2026-11-24, target 65 °F)

| Time | Event |
|---|---|
| 12:15:32 | Thermostat set to Heat with the sensor at 54.9 °F; start sent and confirmed at 12:15:34 |
| 12:19:59 | Running; output raised from 3.0 to 5.0 kW at 12:20:01 |
| 12:29:13 | Sensor 61.0 °F: output 4.1 kW |
| 12:30:23 | Sensor 61.2 °F: output 3.9 kW (70 s after the previous change) |
| 12:31:23 | Sensor 61.4 °F: 3.8 kW wanted, under the 0.2 kW minimum change, not sent |
| 12:31:51 | Sensor 62.4 °F: output 3.1 kW |
| 12:33:31 | Sensor jumped to 71.4 °F: output 1.4 kW, then stop, both confirmed within one 2 s poll; thermostat stayed in Heat |
| 12:38:04 | Off after a 4:33 cool-down; thermostat idle in Heat |
| 12:49:13 | Second run started by the thermostat |
| 12:53:21 | Running; output raised from 1.4 to 5.0 kW |
| 12:54:06 | Heater stopped from the phone app; thermostat switched itself to Off without sending anything, and stayed Off with a cold sensor |

Every output value matched the formula in 9.4. During startup the heater
follows its own output ramp regardless of the setting (2.0–2.7 kW against a
1.4 kW setting).

### 6.3 Firmware reply

A notification with `0x0E` at byte 1 carries the version in bytes 5, 6, and 7,
formatted as `V<byte5>.<byte6>.<byte7>`.

Observed reply, decoding as **V2.1.2**:

```text
40 0E 01 01 01 02 01 02 56
```

Bytes 2–4 (`01 01 01`) match the trailing bytes of the advertised manufacturer
data. They may identify the hardware model, which is unconfirmed.

---

## 7. Fault Codes

| Raw | Display | Fault |
|---|---|---|
| `0xFF` | — | No fault |
| 0 | E0 | Motherboard failure |
| 1 | E1 | Startup failure |
| 2 | E2 | Flame extinguished |
| 3 | E3 | Abnormal voltage |
| 4 | E4 | Premature ignition |
| 6 | E6 | Mainboard sensor failure |
| 7 | E7 | Fuel pump failure |
| 8 | E8 | Motherboard failure |
| 9 | E9 | Glow plug failure |
| 10 | E10 | Overheating |
| 11 | E11 | Heater sensor failure |
| 12 | E12 | Ignition plug disconnected |
| 16 | C4 | Control panel failure |
| 17 | C7/CONN | Control panel / main board failure |

Codes 5 and 13–15 have no defined meaning. Any undefined value should be reported
as `Unknown (<raw>)` and still count as a fault.

The frame carries only the current fault. Home Assistant history provides the
fault log.

---

## 8. Onboard Schedule

The heater stores a daily schedule of 24 one-hour slots. Selecting an hour starts
the heater at that hour and shuts it down one hour later. For example, selecting
01:00 and 05:00 runs it during 01:00–02:00 and 05:00–06:00 every day.

Hours are set with command `0x0B`, followed 300 ms later by the enable command
`0x0F`. The BougeRV app labels bit *n* of `H0`, `H1`, and `H2` as the slots
(*n* + 1):00, (*n* + 9):00, and (*n* + 17):00, so bit 0 of `H0` is 01:00 and
bit 7 of `H2` is 24:00 (midnight). The integration reports hours with these
labels. Whether the firmware fires slot 01:00 at 1 a.m. has not been observed.

Schedule editing and clock sync (`0x0A`) are intentionally **not** implemented:
Home Assistant automations and the thermostat replace them. The schedule state
stays visible because it can start the heater with no client connected.

**The heater can start itself from this schedule without any connected client.**
The integration should therefore surface the schedule state even if it does not
offer schedule editing.

---

## 9. Proposed Integration Design

### 9.1 Release phases

| Phase | Version | Scope |
|---|---|---|
| 1 | 0.1 | Read-only: all sensors, faults with causes and recovery steps, MAC-confirmed setup |
| 2 | 0.2 | Power and heat output controls, disabled by default, with the guards in 9.3 |
| 3 | 0.3 | Home Assistant thermostat. Onboard schedule editing and clock sync are **not planned**: automations and the thermostat replace them, and the schedule stays read-only |

Phase 1 writes nothing to the heater except the firmware query.

### 9.2 Entities (phase 1)

| Entity | Platform | Default | Source |
|---|---|---|---|
| Run state | `sensor` (enum) | Enabled | Byte 2 |
| Heat output | `sensor` (kW) | Enabled | Byte 5 |
| Heat output setting | `sensor` (kW) | Enabled | Byte 12 |
| Combustion efficiency | `sensor` (%) | Enabled | Byte 4 |
| Input voltage | `sensor` (V; unknown while off) | Enabled | Byte 14 |
| Total run time | `sensor` (h, total increasing) | Enabled | Bytes 9–10 |
| Fault | `binary_sensor` (problem) | Enabled | Byte 3 |
| Fault code | `sensor` (`none` or label; name, causes, recovery steps as attributes) | Enabled | Byte 3 |
| Onboard schedule | `binary_sensor`, hours attribute | Enabled | Bytes 11, 15–17 |
| Fan, fuel pump, glow plug, flame, intake, exhaust | `binary_sensor` (running, diagnostic) | Enabled | Byte 2 |
| Signal strength | `sensor` (dBm, diagnostic, polled every 60 s) | Disabled | Advertisements |
| Status frame | `sensor` (hex, diagnostic; unknown bytes as attributes) | Disabled | Whole frame |
| Firmware | Device registry | — | Firmware reply |

Byte 6 is not room temperature and is not exposed except through the status
frame entity and diagnostics.

### 9.2c Controls (phase 2, v0.2)

| Entity | Platform | Default | Command | Confirmed when |
|---|---|---|---|---|
| Power | `switch` (on = Starting or Running) | Disabled | `0x05` | On: run state leaves Off. Off: run state is Shutting down or Off |
| Heat output setting | `number` (1.4–5.0 kW, step 0.1, slider) | Disabled | `0x07` | Byte 12 equals the requested value |

Each command takes a lock, rechecks the guards in 9.3 against the latest
status, writes once with response, and waits up to 10 s (power) or 6 s
(output) for a confirming status frame. It is never retried automatically. A
request that already matches the heater's state is not sent. Every command and
its confirmation or failure is logged at info or warning level.

Guard refusals are raised as validation errors with translated messages. A
missing connection, a failed write, or a missing confirmation is raised as an
error.

### 9.2a Setup and MAC confirmation

The heater has no pairing or password, so setup is the only safeguard against
adding a neighbor's heater. Both the discovery and manual paths show the MAC
address and refuse to finish until the user confirms it. The BougeRV app shows
the MAC on the heater's settings page. Manual setup lists heaters strongest
signal first, and Pro heaters are never offered.

### 9.2b Connection behavior

- Held-open connection with automatic reconnect: 2 s after a drop that had
  data, otherwise backing off from 5 s to 60 s. While the heater is not
  advertising, the integration waits for its next advertisement.
- A session that receives no status for 10 s is dropped as stale.
- Unchanged status frames are not republished, so the 1 Hz stream does not
  flood the recorder.
- Frames that fail the checksum are discarded and counted in diagnostics.

### 9.3 Control guards (phase 2)

- **Power on** only when the latest status is fresh, the run state is Off, and no
  fault is active.
- **Power off** from Running, or whenever the state bits are `01` and a fault is
  active (the app shows the off button in that case too). Refused during a
  normal startup and during cool-down, until power-off during startup has been
  tested (checklist 9).
- **Heat output** only within 14–50 raw, while Starting or Running with no fault.
- Confirm every command from the status stream rather than assuming it applied.
- Do not send schedule, mode, or temperature commands in phase 2.

### 9.4 Thermostat (phase 3, v0.3)

The standard heater has **no onboard thermostat or room sensor**. The Pro's
thermostat depends on a room sensor in its remote. An equivalent can run in
Home Assistant as a `climate` entity driven by an external temperature sensor
that the user selects:

- **Modulation (primary):** while running, adjust output between 1.4 and
  5.0 kW in proportion to the distance from the setpoint. This avoids ignition
  cycles.
- **On/off (secondary):** stop only after running at minimum output and
  exceeding the setpoint by a margin (the Pro uses +2 °C). Restart only below
  the setpoint minus a margin, and only from Off.
- **Anti-cycling:** enforce minimum run and off times, because every start
  costs a glow-plug ignition and a 1–5 minute cool-down.
- **Fail-safe:** on a stale or unavailable temperature sensor, shut down. After
  a failed start (E1), never retry automatically.
- **Limitation:** the logic lives in Home Assistant. If Home Assistant or the
  BLE link fails while the heater is running, it keeps burning at its last
  output with no thermostat cutoff. The Pro's thermostat does not have this
  weakness.

**Implementation (v0.3):** `climate` entity, disabled by default, created only
when a temperature sensor is chosen in the integration options. Modes Off and
Heat; target 5–30 °C in whole-degree steps, shown in whole degrees of the system unit. The control
logic is a pure state machine (`thermostat.py`), evaluated on every heater
status change, every sensor change, and every 30 s; each evaluation takes at
most one action.

| Setting (options) | Default | Notes |
|---|---|---|
| Temperature sensor | — | Required for the thermostat to exist |
| Minimum run time | 15 min | Before a stop for being above target |
| Minimum off time | 10 min | Before a restart |
| Stop margin | 2 °C | Above target, at minimum output |
| Restart margin | 1 °C | Below target |
| Sensor timeout | 15 min | Unavailable or not reporting for this long counts as stale |

| Situation | Behavior |
|---|---|
| Heat, heater off, room ≤ target − restart margin | Start (after the minimum off time) |
| Heat, running | Output = 1.4 kW + (target − room) / 3 °C × 3.6 kW, clamped to 1.4–5.0, rounded to 0.1. Sent only if it differs by ≥ 0.2 kW (or reaches a limit), at most once a minute |
| Heat, running, room ≥ target + stop margin, output at 1.4 kW | Stop, once the room has stayed above the margin at minimum output for **3 minutes** (resetting whenever it drops below or the sensor has no reading) and after the minimum run time |
| Heat, sensor stale | Stop a running heater; wait while starting; resume when the sensor returns |
| Heat, heater fault, or a start the heater did not confirm | Lockout: no action until the thermostat is turned off and on |
| Heat, heater stopped by the app or its panel | Thermostat switches to Off |
| Off | Stops a heater the thermostat was running (waiting until a startup reaches Running); otherwise leaves starts from elsewhere alone |
| Link lost | No action; the next status is judged on its own, so a reconnect is not mistaken for a stop |

Mode, target, and the last start and stop times survive a restart. The
thermostat acts only once heater status and the sensor are both available. An
onboard schedule that is on **with hours selected** raises a Home Assistant
repair issue, because it can start the heater while the thermostat is off.
The `control` attribute explains the current decision and `lockout` its reason.

### 9.5 Safety behavior

- Control entities stay disabled until the user opts in, because an automation can
  start a combustion appliance with nobody present.
- A lost BLE link makes all entities **unavailable**. It must never be reported as
  Off, because the heater may still be burning.
- The shutdown cool-down must remain visible. Battery power must not be cut until
  the run state returns to Off, since removing power early can damage the heater.
- The protocol has no application-layer authentication, so any BLE client in range
  may be able to issue commands. The integration cannot mitigate this.

---

## 10. Hardware Validation Checklist

Steps 1–5 send no power, output, or schedule commands.

| # | Item | Status |
|---|---|---|
| 1 | Advertised name and service UUID | **Done:** `BougeRV-DHG001`, no service UUID |
| 2 | GATT table, write type, encryption over a local adapter | **Done:** no pairing needed |
| 2b | Same connection through an ESPHome Bluetooth proxy | **Done:** works unbonded through an ESPHome proxy; HA failed over to it automatically |
| 3 | Firmware query | **Done:** V2.1.2 |
| 4 | Status stream: clock dependence, rate, link stability, frame length, checksum | **Done** (Off state only) |
| 4b | Run-cycle behavior, voltage while running | **Done** (section 6.2a); bytes 6, 7, 8, 18, and byte 11 bit 0 still unexplained |
| 5 | Whether a held connection blocks the phone app | **Done:** concurrent centrals work |
| 5b | Integration through Home Assistant's Bluetooth stack | **Done:** stable, no drops or bad frames |
| 6 | Output changes while running | **Done:** 5.0 → 3.0 kW confirmed, output followed |
| 7 | Power on, power off, and full cool-down | **Done** from Home Assistant (section 6.2b) |
| 8 | Heater behavior when the BLE link drops in each run state | Running and Shutting down: **heater carries on unaffected**. Starting: untested |
| 9 | Whether power-off is accepted during startup | Open, **attended only** |
