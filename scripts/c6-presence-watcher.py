#!/usr/bin/env python3
"""
c6-presence-watcher.py — ADR-125 iter 2.

Bridges real ESP32-C6 ADR-081 `rv_feature_state` UDP frames to the HAP
`MotionSensor` characteristic via the toggle file that
`scripts/hap-test-sensor.py` already pairs against. No mocks, no
simulation — consumes the exact 60-byte struct emitted by
`firmware/esp32-csi-node/main/rv_feature_state.[ch]`.

Wire format (RV_FEATURE_STATE_MAGIC = 0xC5110006, 60 bytes total,
__attribute__((packed))):

    offset  size  field            type
    0       4     magic            u32   = 0xC5110006
    4       1     node_id          u8
    5       1     mode             u8
    6       2     seq              u16
    8       8     ts_us            u64
    16      4     motion_score     f32   0..1, 100 ms window
    20      4     presence_score   f32   0..1, 1 s window
    24      4     respiration_bpm  f32
    28      4     respiration_conf f32
    32      4     heartbeat_bpm    f32
    36      4     heartbeat_conf   f32
    40      4     anomaly_score    f32
    44      4     env_shift_score  f32
    48      4     node_coherence   f32
    52      2     quality_flags    u16
    54      2     reserved         u16
    56      4     crc32            u32

`quality_flags & RV_QFLAG_PRESENCE_VALID (1<<0)` gates presence reads.
`presence_score >= PRESENCE_THRESHOLD` toggles motion ON; below the
release threshold (with hysteresis) toggles OFF. The toggle file
is the contract between this watcher and the paired HAP bridge.

Usage:
    python3 c6-presence-watcher.py [--port 5005] [--toggle /tmp/ruview-motion]
"""
from __future__ import annotations
import argparse
import json
import math
import os
import signal
import socket
import struct
import statistics
import sys
import time
import zlib
from collections import defaultdict, deque

RV_FEATURE_STATE_MAGIC = 0xC5110006
CSI_RAW_MAGIC = 0xC5110001
RV_QFLAG_PRESENCE_VALID = 1 << 0
PACKET_SIZE = 60


class PrivacyClass:
    """Mirror of `wifi-densepose-bfld::PrivacyClass` (Rust, ADR-118 §2.1).

    The HAP boundary is governed by ADR-125 §2.1.d + ADR-122 §2.4: only
    `Anonymous` (2) and `Restricted` (3) frames may cross. `Raw` (0) and
    `Derived` (1) are HAP-ineligible by structural invariant I1.
    """
    RAW = 0
    DERIVED = 1
    ANONYMOUS = 2
    RESTRICTED = 3

    _names = {RAW: "Raw", DERIVED: "Derived", ANONYMOUS: "Anonymous",
              RESTRICTED: "Restricted"}

    @classmethod
    def name(cls, value: int) -> str:
        return cls._names.get(value, f"Unknown({value})")

    @classmethod
    def from_str(cls, s: str) -> int:
        m = {"raw": cls.RAW, "derived": cls.DERIVED,
             "anonymous": cls.ANONYMOUS, "restricted": cls.RESTRICTED}
        if s.lower() not in m:
            raise ValueError(f"invalid privacy class {s!r}; "
                             f"expected one of {list(m.keys())}")
        return m[s.lower()]

    @classmethod
    def allows_hap(cls, value: int) -> bool:
        """ADR-125 §2.1.d gate: only class-2/3 cross the HomeKit boundary."""
        return value in (cls.ANONYMOUS, cls.RESTRICTED)


# Semantic-event naming per ADR-125 §2.1.d. The HAP bridge keeps
# advertising a generic MotionSensor; this is the operator-facing
# *label* for the event, written into the watcher log + summary line
# so the operator never sees "intruder detected" framing.
SEMANTIC_EVENT_UNKNOWN_PRESENCE = "Unknown Presence"

# Hysteresis — entry / exit thresholds keep the HomeKit characteristic
# from flapping when presence_score sits near the boundary.
PRESENCE_ON_THRESHOLD = 0.40
PRESENCE_OFF_THRESHOLD = 0.20
# Idle releases motion after this many seconds with no valid presence
# packets (covers the C6 falling off the air entirely).
IDLE_RELEASE_S = 5.0

# 60-byte packed layout (`<` = little-endian + no padding)
# magic|node|mode|seq|ts|motion|presence|resp_bpm|resp_c|hb_bpm|hb_c|anom|env|coh|qflags|reserved|crc
PACKET_STRUCT = struct.Struct("<IBBHQfffffffffHHI")
assert PACKET_STRUCT.size == PACKET_SIZE, (
    f"layout mismatch: struct {PACKET_STRUCT.size}, expected {PACKET_SIZE}"
)


def parse_packet(buf: bytes):
    """Return parsed dict or None if not a feature_state packet."""
    if len(buf) != PACKET_SIZE:
        return None
    fields = PACKET_STRUCT.unpack(buf)
    (magic, node_id, mode, seq, ts_us, motion, presence,
     resp_bpm, resp_conf, hb_bpm, hb_conf,
     anomaly, env_shift, coherence,
     qflags, _reserved, crc) = fields
    if magic != RV_FEATURE_STATE_MAGIC:
        return None
    # CRC32 over bytes [0..end-4]. Firmware uses IEEE poly == zlib.crc32.
    expected = zlib.crc32(buf[:-4]) & 0xFFFFFFFF
    crc_ok = expected == crc
    return {
        "node_id": node_id, "mode": mode, "seq": seq, "ts_us": ts_us,
        "motion": motion, "presence": presence,
        "resp_bpm": resp_bpm, "resp_conf": resp_conf,
        "hb_bpm": hb_bpm, "hb_conf": hb_conf,
        "anomaly": anomaly, "env_shift": env_shift, "coherence": coherence,
        "qflags": qflags, "crc_ok": crc_ok,
        "presence_valid": bool(qflags & RV_QFLAG_PRESENCE_VALID),
    }


def csi_signature(buf: bytes):
    """Return a small normalized ADR-018 amplitude signature, never raw IQ."""
    if len(buf) < 60 or struct.unpack_from('<I', buf)[0] != CSI_RAW_MAGIC:
        return None
    node_id, antennas, subcarriers = struct.unpack_from('<BBH', buf, 4)
    frequency = struct.unpack_from('<I', buf, 8)[0]
    rssi = struct.unpack_from('<b', buf, 16)[0]
    expected = 20 + 2 * antennas * subcarriers
    if antennas != 1 or not 32 <= subcarriers <= 512 or len(buf) != expected:
        return None
    iq = memoryview(buf)[20:]
    amplitudes = []
    for index in range(2, subcarriers - 2, 4):
        i = struct.unpack_from('<b', iq, index * 2)[0]
        q = struct.unpack_from('<b', iq, index * 2 + 1)[0]
        amplitudes.append(math.hypot(i, q))
    if not amplitudes or statistics.mean(amplitudes) < 2:
        return None
    scale = statistics.mean(amplitudes)
    return (node_id, frequency, subcarriers, rssi // 5), tuple(a / scale for a in amplitudes), rssi


def set_motion(toggle_file: str, on: bool, current: bool,
               semantic: str = SEMANTIC_EVENT_UNKNOWN_PRESENCE) -> bool:
    """Touch / unlink the toggle file iff state changes. Return new state."""
    if on == current:
        return current
    if on:
        with open(toggle_file, "w") as fh:
            fh.write("1\n")
    else:
        try:
            os.unlink(toggle_file)
        except FileNotFoundError:
            pass
    label = semantic if on else f"clear {semantic}"
    print(f"[{time.strftime('%H:%M:%S')}] {label} (motion -> {on})",
          flush=True)
    return on


def apply_privacy_gate(pkt: dict, allowed_class: int) -> dict | None:
    """ADR-118 PrivacyGate equivalent at the HAP boundary.

    The C6 emits sensor-aggregate `feature_state` frames — *not* raw BFI,
    *not* identity embeddings. We classify the emit at the chosen
    operator class. Returns the (possibly redacted) event dict, or
    `None` if the class doesn't allow HAP crossing.
    """
    if not PrivacyClass.allows_hap(allowed_class):
        return None
    # `Restricted` (3) strips anything that could be a per-occupant
    # fingerprint — even though feature_state currently carries none.
    # Future iters extending the wire format will need to respect this.
    if allowed_class == PrivacyClass.RESTRICTED:
        return {
            "presence": pkt["presence"], "motion": pkt["motion"],
            "presence_valid": pkt["presence_valid"],
            "node_id": pkt["node_id"], "seq": pkt["seq"],
            # anomaly_score / env_shift / coherence dropped (could
            # reveal longitudinal drift signatures over time).
        }
    # `Anonymous` (2) — production default. Carries the aggregate
    # vitals so HomeKit `Unknown Presence` automations can pick up
    # context, but no identity-derived fields.
    return {
        "presence": pkt["presence"], "motion": pkt["motion"],
        "presence_valid": pkt["presence_valid"],
        "node_id": pkt["node_id"], "seq": pkt["seq"],
        "resp_bpm": pkt["resp_bpm"], "hb_bpm": pkt["hb_bpm"],
        "anomaly": pkt["anomaly"], "env_shift": pkt["env_shift"],
        "coherence": pkt["coherence"],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5005)
    p.add_argument("--toggle", default="/tmp/ruview-motion")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--privacy-class", default="anonymous",
                   choices=["raw", "derived", "anonymous", "restricted"],
                   help="ADR-118 PrivacyClass; only anonymous/restricted "
                        "may cross the HAP boundary (ADR-125 §2.1.d).")
    p.add_argument("--state-json", default="/tmp/ruview-state.json",
                   help="JSON state IPC file written for the HAP daemon. "
                        "Contains motion/occupancy/anomaly_ts.")
    p.add_argument("--occupancy-window", type=float, default=3.0,
                   help="Seconds of rolling presence_score average for "
                        "OccupancyDetected (vs short-window MotionDetected).")
    p.add_argument("--anomaly-threshold", type=float, default=0.7,
                   help="anomaly_score crossing this fires the "
                        "'Unrecognized Activity Pattern' event "
                        "(Restricted class only; ADR-125 §2.1.d).")
    args = p.parse_args()

    privacy_class = PrivacyClass.from_str(args.privacy_class)
    if not PrivacyClass.allows_hap(privacy_class):
        sys.stderr.write(
            f"REFUSED: privacy class {PrivacyClass.name(privacy_class)} "
            f"(value={privacy_class}) is not HAP-eligible. "
            f"ADR-125 §2.1.d structural invariant I1: only Anonymous (2) "
            f"and Restricted (3) frames may cross the HomeKit boundary. "
            f"Use --privacy-class anonymous (default) or restricted.\n"
        )
        return 2

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((args.bind, args.port))
    sock.settimeout(1.0)

    print(f"[c6-presence] listening udp {args.bind}:{args.port}", flush=True)
    print(f"[c6-presence] toggle file: {args.toggle}", flush=True)
    print(f"[c6-presence] thresholds: on>={PRESENCE_ON_THRESHOLD}, "
          f"off<={PRESENCE_OFF_THRESHOLD}, idle_release={IDLE_RELEASE_S}s",
          flush=True)
    print(f"[c6-presence] privacy class: "
          f"{PrivacyClass.name(privacy_class)} (HAP-eligible)", flush=True)
    print(f"[c6-presence] semantic event: {SEMANTIC_EVENT_UNKNOWN_PRESENCE}",
          flush=True)

    running = True
    def _stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    motion = os.path.exists(args.toggle)
    occupancy = False
    last_anomaly_ts = 0.0
    last_packet_ts = 0.0
    last_summary = time.time()
    n_total = n_valid = n_crc_bad = n_anomaly_fires = 0
    presence_sum = motion_sum = 0.0
    # Rolling window of (timestamp, presence_score) for occupancy detect
    occ_window: deque[tuple[float, float]] = deque()
    OCC_ON_THRESH = 0.30
    OCC_OFF_THRESH = 0.15
    state_path = args.state_json

    def write_state(motion: bool, occupancy: bool, anomaly_ts: float) -> None:
        try:
            tmp = state_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"motion": motion, "occupancy": occupancy,
                           "anomaly_ts": anomaly_ts, "ts": time.time()}, fh)
            os.replace(tmp, state_path)
        except OSError:
            pass

    # Companion contract for `scripts/ruview-sensing-server.py` (the
    # @ruvnet/rvagent compatibility layer): write the full BFLD-gated
    # feature snapshot so the sensing-server can serve EdgeVitalsMessage
    # and BfldScanResponse without going back to the wire.
    feature_path = "/tmp/ruview-last-feature.json"
    signal_path = "/tmp/ruview-csi-signal.json"
    last_signatures = {}
    csi_changes: deque[tuple[float, float]] = deque()
    csi_rssis: deque[tuple[float, int]] = deque()
    node_changes: dict[int, deque[tuple[float, float]]] = defaultdict(deque)
    node_rssis: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
    last_signal_write = 0.0

    def write_feature(gated: dict, motion: bool, occupancy: bool,
                      privacy_cls: int) -> None:
        try:
            payload = {
                    "node_id": str(gated["node_id"]),
                    "timestamp_ms": int(time.time() * 1000),
                    "presence": occupancy,           # sustained
                    "motion": gated["motion"],        # 0..1 float
                    "presence_score": gated["presence"],
                    "n_persons": 1 if occupancy else 0,
                    "confidence": min(1.0, max(0.0, gated["motion"])),
                    "breathing_rate_bpm": (gated["resp_bpm"]
                                           if gated.get("resp_bpm") else None),
                    "heartrate_bpm": (gated["hb_bpm"]
                                      if gated.get("hb_bpm") else None),
                    "anomaly_score": gated.get("anomaly"),
                    "privacy_class": privacy_cls,
                    "ts": time.time(),
                }
            # Keep the legacy latest snapshot, but retain each node separately
            # so concurrent ESPs do not hide one another in the registry.
            node_path = os.path.join(os.path.dirname(feature_path),
                                     f"ruview-node-{gated['node_id']}-feature.json")
            for path in (feature_path, node_path):
                tmp = path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(payload, fh)
                os.replace(tmp, path)
        except OSError:
            pass

    while running:
        try:
            buf, _addr = sock.recvfrom(2048)
        except socket.timeout:
            buf = None

        now = time.time()

        if buf is not None:
            n_total += 1
            signature = csi_signature(buf)
            if signature is not None:
                key, amplitudes, rssi = signature
                previous = last_signatures.get(key)
                last_signatures[key] = amplitudes
                if previous is not None:
                    # Median normalized spectral change avoids a handful of
                    # unstable subcarriers dominating the room activity trace.
                    change = 100 * statistics.median(
                        abs(current - old)
                        for current, old in zip(amplitudes, previous))
                    csi_changes.append((now, change))
                    csi_rssis.append((now, rssi))
                    node_changes[key[0]].append((now, change))
                    node_rssis[key[0]].append((now, rssi))
                if len(last_signatures) > 128:
                    last_signatures.clear()
            pkt = parse_packet(buf)
            if pkt is not None:
                if not pkt["crc_ok"]:
                    n_crc_bad += 1
                else:
                    # ADR-118 PrivacyGate: classify + redact before the
                    # HAP boundary. Returns None for non-eligible classes.
                    gated = apply_privacy_gate(pkt, privacy_class)
                    if gated is not None and gated["presence_valid"]:
                        n_valid += 1
                        presence_sum += gated["presence"]
                        motion_sum += gated["motion"]
                        last_packet_ts = now
                        # MotionDetected — short-window (each packet)
                        prev_motion = motion
                        if not motion and gated["presence"] >= PRESENCE_ON_THRESHOLD:
                            motion = set_motion(args.toggle, True, motion)
                        elif motion and gated["presence"] <= PRESENCE_OFF_THRESHOLD:
                            motion = set_motion(args.toggle, False, motion)

                        # OccupancyDetected — rolling-window avg (§2.1.d
                        # "Unexpected Occupancy" is a future iter; for now
                        # we expose Occupancy as sustained presence).
                        occ_window.append((now, gated["presence"]))
                        cutoff = now - args.occupancy_window
                        while occ_window and occ_window[0][0] < cutoff:
                            occ_window.popleft()
                        if occ_window:
                            occ_avg = (sum(p for _, p in occ_window)
                                       / len(occ_window))
                            if not occupancy and occ_avg >= OCC_ON_THRESH:
                                occupancy = True
                                print(f"[{time.strftime('%H:%M:%S')}] "
                                      f"Unknown Presence — Occupancy ON "
                                      f"(rolling_avg={occ_avg:.2f})",
                                      flush=True)
                            elif occupancy and occ_avg <= OCC_OFF_THRESH:
                                occupancy = False
                                print(f"[{time.strftime('%H:%M:%S')}] "
                                      f"Occupancy OFF "
                                      f"(rolling_avg={occ_avg:.2f})",
                                      flush=True)

                        # Anomaly — only when class allows (Restricted
                        # gate drops anomaly_score entirely; the dict
                        # missing the key is the type-level enforcement).
                        if ("anomaly" in gated
                                and gated["anomaly"] >= args.anomaly_threshold):
                            last_anomaly_ts = now
                            n_anomaly_fires += 1
                            print(f"[{time.strftime('%H:%M:%S')}] "
                                  f"Unrecognized Activity Pattern "
                                  f"(anomaly={gated['anomaly']:.2f})",
                                  flush=True)

                        if (motion != prev_motion
                                or not state_path.endswith(".disabled")):
                            write_state(motion, occupancy, last_anomaly_ts)
                            write_feature(gated, motion, occupancy,
                                          privacy_class)

        while csi_changes and csi_changes[0][0] < now - 2:
            csi_changes.popleft()
        while csi_rssis and csi_rssis[0][0] < now - 2:
            csi_rssis.popleft()
        for node_id, samples in list(node_changes.items()):
            while samples and samples[0][0] < now - 2:
                samples.popleft()
            rssis = node_rssis[node_id]
            while rssis and rssis[0][0] < now - 2:
                rssis.popleft()
            if not samples:
                node_changes.pop(node_id, None)
                node_rssis.pop(node_id, None)
        if now - last_signal_write >= 1 and len(csi_changes) >= 5:
            try:
                tmp = signal_path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump({"ts": now,
                               "change": round(statistics.median(v for _, v in csi_changes), 3),
                               "sample_count": len(csi_changes),
                               "mean_rssi": round(statistics.mean(v for _, v in csi_rssis), 1)}, fh)
                os.replace(tmp, signal_path)
            except OSError:
                pass
            for node_id, samples in node_changes.items():
                if len(samples) < 5 or not node_rssis[node_id]:
                    continue
                path = os.path.join(os.path.dirname(signal_path),
                                    f"ruview-node-{node_id}-signal.json")
                try:
                    tmp = path + ".tmp"
                    with open(tmp, "w") as fh:
                        json.dump({"node_id": node_id, "ts": now,
                                   "change": round(statistics.median(v for _, v in samples), 3),
                                   "sample_count": len(samples),
                                   "mean_rssi": round(statistics.mean(v for _, v in node_rssis[node_id]), 1)}, fh)
                    os.replace(tmp, path)
                except OSError:
                    pass
            last_signal_write = now

        # Idle release — if the C6 stops sending entirely, clear motion
        # AND occupancy.
        if motion and last_packet_ts and (now - last_packet_ts) > IDLE_RELEASE_S:
            motion = set_motion(args.toggle, False, motion)
            occupancy = False
            occ_window.clear()
            write_state(motion, occupancy, last_anomaly_ts)

        # Periodic summary line (every 10 s) so we can see the watcher is alive
        if now - last_summary >= 10.0:
            avg_p = presence_sum / n_valid if n_valid else 0.0
            avg_m = motion_sum / n_valid if n_valid else 0.0
            print(
                f"[{time.strftime('%H:%M:%S')}] 10s stats: "
                f"pkts={n_total} valid={n_valid} crc_bad={n_crc_bad} "
                f"avg_presence={avg_p:.2f} avg_motion={avg_m:.2f} "
                f"motion={motion} occupancy={occupancy} "
                f"anomaly_fires={n_anomaly_fires}",
                flush=True,
            )
            n_total = n_valid = n_crc_bad = n_anomaly_fires = 0
            presence_sum = motion_sum = 0.0
            last_summary = now

    sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
