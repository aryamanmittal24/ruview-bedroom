#!/usr/bin/env python3
"""Listen for local WiZ SpaceSense (src=pir) events and publish room state."""
from __future__ import annotations

import json
import os
import random
import select
import socket
import time

PORT = 38899
PUSH_PORT = 38900
STATE_FILE = "/tmp/ruview-wiz-spacesense.json"
KNOWN = {
    "9877d5b808a8": "Floor Lamp",
    "9877d5e13eae": "Left Bed Bulb",
    "9877d5e82458": "Right Bed Bulb",
    "9877d564d806": "Table Lamp",
}
IP_HINTS = {
    "9877d5b808a8": "192.168.0.9",
    "9877d5e13eae": "192.168.0.10",
    "9877d5e82458": "192.168.0.11",
    "9877d564d806": "192.168.0.12",
}


def local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.168.0.1", 80))
        return probe.getsockname()[0]


def write_state(devices: dict, occupancy: bool | None, event_ts: float | None) -> None:
    now = time.time()
    bulbs = []
    for mac, name in KNOWN.items():
        item = devices.get(mac, {})
        bulbs.append({
            "mac": mac, "name": name, "ip": item.get("ip"),
            "online": now - item.get("seen", 0) < 20,
            "rssi": item.get("rssi"),
            "registered": now - item.get("registered_at", 0) < 30,
            "last_push_ts": item.get("last_push_ts"),
            "last_push_source": item.get("last_push_source"),
        })
    body = {
        "ts": now,
        "online_count": sum(1 for bulb in bulbs if bulb["online"]),
        "configured_count": len(KNOWN),
        "occupancy": occupancy,
        "occupancy_known": occupancy is not None,
        "last_spacesense_event_ts": event_ts,
        "bulbs": bulbs,
    }
    temporary = STATE_FILE + ".tmp"
    with open(temporary, "w") as fh:
        json.dump(body, fh)
    os.replace(temporary, STATE_FILE)


def main() -> None:
    source_ip = local_ip()
    phone_mac = f"02CC{random.randrange(2**32):08X}"
    registration = json.dumps({
        "method": "registration",
        "params": {"phoneIp": source_ip, "phoneMac": phone_mac, "register": True},
    }).encode()
    config_request = json.dumps({"method": "getSystemConfig", "params": {}}).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", PUSH_PORT))
    sock.settimeout(0.25)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    devices: dict[str, dict] = {
        mac: {"ip": ip, "seen": 0} for mac, ip in IP_HINTS.items()
    }
    occupancy: bool | None = None
    event_ts: float | None = None
    next_register = 0.0
    next_scan = 0.0
    next_write = 0.0
    next_poll = 0.0
    print(f"[wiz-spacesense] listening UDP {PUSH_PORT} from {source_ip}", flush=True)
    def send(payload: bytes, ip: str) -> None:
        try:
            sender.sendto(payload, (ip, PORT))
        except OSError:
            pass

    def poll_bulb(ip: str) -> dict | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as query:
                query.settimeout(0.35)
                query.sendto(b'{"method":"getPilot","params":{}}', (ip, PORT))
                raw, _ = query.recvfrom(8192)
                return json.loads(raw).get("result")
        except (OSError, socket.timeout, json.JSONDecodeError):
            return None

    while True:
        now = time.time()
        if now >= next_scan:
            for item in devices.values():
                send(config_request, item["ip"])
            next_scan = now + 60
        if now >= next_register:
            for item in devices.values():
                send(registration, item["ip"])
            send(registration, "255.255.255.255")
            next_register = now + 4
        if now >= next_poll:
            for mac, item in devices.items():
                result = poll_bulb(item["ip"])
                if result and result.get("mac") == mac:
                    item.update(seen=time.time(), rssi=result.get("rssi"))
            next_poll = now + 4
        try:
            ready, _, _ = select.select([sock, sender], [], [], 0.25)
            if not ready:
                raise socket.timeout()
            raw, address = ready[0].recvfrom(16384)
            message = json.loads(raw)
            payload = message.get("result") or message.get("params") or {}
            mac = payload.get("mac") or next((key for key, item in devices.items() if item.get("ip") == address[0]), None)
            if mac in KNOWN:
                item = devices.setdefault(mac, {})
                item.update(ip=address[0], seen=now)
                if message.get("method") == "registration" and payload.get("success") is True:
                    item["registered_at"] = now
                if message.get("method") == "syncPilot":
                    item.update(last_push_ts=now, last_push_source=payload.get("src"))
                if payload.get("rssi") is not None:
                    item["rssi"] = payload["rssi"]
                if message.get("method") == "syncPilot" and payload.get("src") == "pir" and isinstance(payload.get("state"), bool):
                    occupancy = payload["state"]
                    event_ts = now
                    print(f"[wiz-spacesense] occupancy={occupancy} via {KNOWN[mac]}", flush=True)
        except socket.timeout:
            pass
        except (json.JSONDecodeError, OSError):
            pass
        if now >= next_write:
            write_state(devices, occupancy, event_ts)
            next_write = now + 1


if __name__ == "__main__":
    main()
