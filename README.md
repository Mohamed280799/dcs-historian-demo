# DCS → Historian Demo: OPC UA Interface with Data-Integrity Checks

A small learning project that copies a typical task in a pharma DCS team:
**getting process data from the control system into a historian (like AVEVA PI), and noticing quickly when that link goes wrong.**

- `dcs_simulator.py` acts as a **DCS area** ("Area100") and publishes four tags over **OPC UA**, with faults you can switch on.
- `historian.py` acts as a **historian**: it reads the tags every second, stores value, quality and source timestamp in SQLite, and checks data integrity.
- `report.py` summarises what was stored, every integrity event, the data gaps, and the audit trail.

## What the historian checks

| Check | What it means in a plant | How it is detected |
|---|---|---|
| **STALE** | A value looks normal but is no longer updating (frozen interface, stuck driver) | Source timestamp unchanged for longer than `stale_limit_s` |
| **BAD** | Instrument or interface reports bad data | OPC UA status code is not Good |
| **CONN_LOST / CONN_OK** | Network, server or interface down, then back | Client connection fails; it retries every `retry_s` seconds |
| **Data gap** | Missing history during an outage | Report shows the largest gap between samples per tag |
| **Audit trail** | No setting changes silently in a validated system (ALCOA+: attributable, contemporaneous) | Every config change needs a user and a reason, and is logged with old → new values |

## Example run (tested)

Simulator with faults: TT101 freezes after 15 s, PT102 goes Bad after 25 s, server stops after 40 s and is restarted at ~48 s.

```
CONN_OK   -      connected to opc.tcp://127.0.0.1:53530/dcs_sim/
STALE     TT101  no update for > 6 s
BAD       PT102  quality BadSensorFailure
CONN_LOST -      ConnectionError: retrying every 3 s
CONN_OK   -      connected again
FRESH     TT101  updates resumed
GOOD      PT102  quality back to Good

Largest data gap per tag: 13 s (the outage is visible in the history)
Audit trail: mohamed: stale_limit_s 5.0 -> 6.0 (Detect stale tags within 6 s)
```

## How to run (Windows, VS Code)

```
pip install -r requirements.txt
```

Open **two terminals** in VS Code (Terminal → New Terminal, twice), both in this folder.

**1. Optional: record a configuration change in the audit trail**
```
python historian.py --set stale_limit_s=6 --user mohamed --reason "Detect stale tags within 6 s"
```

**2. Terminal 1: start the DCS simulator with faults**
```
python dcs_simulator.py --freeze TT101:15 --bad PT102:25 --stop-after 40
```

**3. Terminal 2: start the historian**
```
python historian.py --duration 70
```

**4. After the simulator stops (around 40 s), start it again in Terminal 1 without faults:**
```
python dcs_simulator.py
```
The historian reconnects by itself.

**5. When the historian finishes, show the summary:**
```
python report.py
```

Delete `historian.db` to start with a clean history.

**Port:** the demo uses port 53530 by default. If Windows reports *WinError 10013* or the port is busy, pick another one and use the same number in both programs, e.g. `--port 49320` on the simulator and the historian.

## How this maps to a real ABB 800xA + PI setup

| This demo | Real plant |
|---|---|
| `dcs_simulator.py` (OPC UA server) | 800xA connectivity server exposing controller data |
| `historian.py` | PI interface/connector + PI Data Archive |
| `historian.db` | PI archive |
| STALE / BAD / CONN events | Interface health monitoring and alarms |
| Audit trail | Change control and audit trail required by GMP / 21 CFR Part 11 |

## Limitations
- A learning simulation, not a validated system: no OPC UA security (certificates, encryption), no redundancy, polling instead of subscriptions.
- Real PI interfaces buffer data during an outage so history is not lost; here the gap stays visible on purpose.

*Author: Mohamed Yosif Abdo – M.Sc. Electrical Engineering (Automation & Robot Technology), DTU*
