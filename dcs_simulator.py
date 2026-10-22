"""
DCS simulator: a small OPC UA server that behaves like a process area in a DCS.

It publishes four process tags for "Area100" and lets you inject typical faults,
so the historian client can show how integration problems are detected.

Tags (namespace "DCS_Sim", object Area100):
    TT101  temperature   [degC]
    PT102  pressure      [bar]
    FT103  flow          [m3/h]
    P104   pump running  [bool]

Faults (command-line options):
    --freeze TAG:SECONDS   the tag stops updating after SECONDS (stale value)
    --bad TAG:SECONDS      the tag gets Bad quality after SECONDS
    --stop-after SECONDS   the whole server shuts down (lost connection)

Usage:
    python dcs_simulator.py
    python dcs_simulator.py --freeze TT101:20 --bad PT102:30 --stop-after 45
"""
import argparse
import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone

from asyncua import Server, ua

DEFAULT_PORT = 53530  # 4840 is the standard OPC UA port but is often taken (e.g. by Siemens tools)


def endpoint(port):
    return f"opc.tcp://127.0.0.1:{port}/dcs_sim/"
logging.getLogger("asyncua").setLevel(logging.ERROR)


def parse_faults(items):
    """'TT101:20' -> {'TT101': 20.0}"""
    faults = {}
    for item in items or []:
        tag, seconds = item.split(":")
        faults[tag] = float(seconds)
    return faults


async def main():
    ap = argparse.ArgumentParser(description="Simulated DCS area exposed over OPC UA")
    ap.add_argument("--freeze", action="append", help="TAG:SECONDS, value stops updating")
    ap.add_argument("--bad", action="append", help="TAG:SECONDS, quality becomes Bad")
    ap.add_argument("--stop-after", type=float, help="shut the server down after SECONDS")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default {DEFAULT_PORT})")
    args = ap.parse_args()
    freeze, bad = parse_faults(args.freeze), parse_faults(args.bad)

    server = Server()
    await server.init()
    server.set_endpoint(endpoint(args.port))
    server.set_server_name("DCS Simulator (Area100)")
    idx = await server.register_namespace("DCS_Sim")
    area = await server.nodes.objects.add_object(idx, "Area100")

    tags = {
        "TT101": await area.add_variable(idx, "TT101", 65.0),
        "PT102": await area.add_variable(idx, "PT102", 1.20),
        "FT103": await area.add_variable(idx, "FT103", 12.0),
        "P104": await area.add_variable(idx, "P104", True),
    }

    print(f"DCS simulator running on {endpoint(args.port)}")
    if freeze or bad or args.stop_after:
        print(f"Faults planned: freeze={freeze} bad={bad} stop_after={args.stop_after}")

    start = time.monotonic()
    async with server:
        while True:
            t = time.monotonic() - start
            if args.stop_after and t >= args.stop_after:
                print(f"[{t:5.1f}s] FAULT: server shutting down (lost connection)")
                break

            values = {
                "TT101": 65 + 2 * math.sin(t / 10) + random.gauss(0, 0.1),
                "PT102": 1.20 + 0.05 * math.sin(t / 7) + random.gauss(0, 0.005),
                "FT103": 12 + 0.5 * math.sin(t / 5) + random.gauss(0, 0.05),
                "P104": True,
            }
            for name, node in tags.items():
                if name in freeze and t >= freeze[name]:
                    continue  # value and source timestamp stay frozen
                status = ua.StatusCodes.Good
                if name in bad and t >= bad[name]:
                    status = ua.StatusCodes.BadSensorFailure
                vtype = ua.VariantType.Boolean if name == "P104" else ua.VariantType.Double
                dv = ua.DataValue(
                    ua.Variant(values[name], vtype),
                    StatusCode=ua.StatusCode(status),
                    SourceTimestamp=datetime.now(timezone.utc),
                )
                await node.write_value(dv)
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
