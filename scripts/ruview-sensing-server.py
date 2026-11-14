#!/usr/bin/env python3
"""
ruview-sensing-server.py — ADR-125 Tier 1+2 iter 2.

A tiny HTTP server that speaks the subset of the RuView sensing-server
HTTP API that @ruvnet/rvagent (ADR-124, npm v0.1.0) expects, sourced
from the BFLD-gated state files written by c6-presence-watcher.py.

This is the "sensing-server-equivalent" the cron stop condition names,
and it lets any MCP agent (Claude Code via `claude mcp add rvagent`,
Codex with the matching MCP config, custom LLM client) consume the
real ESP32-C6 stream through the same MCP tool surface that the Rust
sensing-server exposes — without needing the Rust binary to be running.

Endpoints (matched against tools/ruview-mcp/src/tools/*.ts):

  GET  /health                                 — liveness
  GET  /api/v1/sensing/latest                  — ADR-102 schema v2
  GET  /api/v1/edge/registry                   — node enumeration
  GET  /api/v1/vitals/<node_id>/latest         — EdgeVitalsMessage
  GET  /api/v1/bfld/<node_id>/last_scan        — BfldScanResponse
  POST /api/v1/bfld/<node_id>/subscribe?duration_s=N — { subscription_id }

The source-of-truth file is `/tmp/ruview-last-feature.json` written
by the watcher on every BFLD-gated feature_state packet. If absent
or stale (> STALENESS_S seconds old), endpoints return 503 with a
hint so the rvagent tool emits a graceful warn shape.

Bearer-token auth is intentionally OFF in this dev surface — the
Rust sensing-server adds it via the #443 middleware; that path is
out of scope for the demo bridge.
"""
from __future__ import annotations
import json
import math
import statistics
import tempfile
import os
import re
import sys
import time
import movement_lab
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

FEATURE_FILE = os.environ.get("RUVIEW_FEATURE_JSON",
                              "/tmp/ruview-last-feature.json")
WIZ_FILE = os.environ.get("RUVIEW_WIZ_JSON",
                          "/tmp/ruview-wiz-spacesense.json")
SIGNAL_FILE = os.environ.get("RUVIEW_CSI_SIGNAL_JSON",
                             "/tmp/ruview-csi-signal.json")
STALENESS_S = 10.0
DEFAULT_PORT = int(os.environ.get("PORT", "3000"))
UI_ROOT = Path(__file__).resolve().parents[1] / "ui"
BASELINE_FILE = Path.home() / ".local/state/ruview/room-baseline.json"


def save_baseline(samples, context="Quiet-room calibration"):
    if not isinstance(samples, list) or not 20 <= len(samples) <= 180:
        raise ValueError("Need 20–180 fresh CSI samples")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in samples):
        raise ValueError("Invalid CSI sample")
    center = statistics.median(samples)
    noise = statistics.median(abs(v - center) for v in samples)
    threshold = max(center + max(.5, noise * 3), sorted(samples)[math.ceil(len(samples) * .95)-1] + .5)
    baseline = {"change": center, "noise": noise, "threshold": threshold,
                "sample_count": len(samples), "ts": time.time(), "context": context,
                "scope": "room-wide signal changes; room boundary not verified"}
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=BASELINE_FILE.parent, delete=False) as fh:
        json.dump(baseline, fh)
    os.replace(fh.name, BASELINE_FILE)
    return baseline


def load_baseline():
    try:
        data = json.loads(BASELINE_FILE.read_text())
        if 0 <= time.time() - data["ts"] < 86400 and all(math.isfinite(data[k]) for k in ("change", "noise", "threshold")):
            return data
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def room_activity(signal, wiz, baseline):
    esp_known = signal is not None and baseline is not None
    energy = min(1, max(0, (signal["change"] - baseline["threshold"]) / max(1, baseline["noise"] * 2))) if esp_known else 0
    # Old occupancy reports must not remain true forever after a bulb disconnects.
    event_age = time.time() - (wiz.get("last_spacesense_event_ts") or 0) if wiz else float("inf")
    wiz_known = bool(wiz and wiz.get("occupancy_known") and 0 <= event_age < 300 and any(b.get("online") and b.get("registered") for b in wiz.get("bulbs", [])))
    wiz_active = wiz_known and wiz.get("occupancy") is True
    active = energy > .25 or wiz_active
    return {"active": active if esp_known or wiz_known else None,
            "energy": max(energy, .68 if wiz_active else 0), "esp_energy": energy,
            "esp_calibrated": esp_known, "wiz_known": wiz_known, "wiz_active": wiz_active,
            "sources": (["ESP32 CSI"] if esp_known else []) + (["WiZ occupancy event"] if wiz_known else []),
            "location_known": False}


def _load_feature(node_id: str | None = None) -> dict | None:
    if node_id is not None:
        if not node_id.isdecimal() or not 1 <= int(node_id) <= 255:
            return None
        path = str(Path(FEATURE_FILE).parent / f"ruview-node-{node_id}-feature.json")
    else:
        path = FEATURE_FILE
    try:
        with open(path, "r") as fh:
            d = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(d, dict):
        return None
    age = time.time() - float(d.get("ts", 0))
    if age > STALENESS_S:
        return None
    if node_id is not None and d.get("node_id") != node_id:
        return None
    return d


def _active_nodes() -> list[dict]:
    nodes = []
    for path in Path(FEATURE_FILE).parent.glob("ruview-node-*-feature.json"):
        node_id = path.name.removeprefix("ruview-node-").removesuffix("-feature.json")
        feature = _load_feature(node_id)
        if feature is not None:
            nodes.append({"node_id": node_id, "kind": "esp32", "online": True,
                          "age_s": round(time.time() - feature["ts"], 2)})
    return sorted(nodes, key=lambda item: int(item["node_id"]))


def _load_wiz() -> dict | None:
    try:
        with open(WIZ_FILE, "r") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("ts", 0)) > 10:
        return None
    return data


def _load_signal() -> dict | None:
    try:
        with open(SIGNAL_FILE, "r") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("ts", 0)) > 5:
        return None
    return data


def vitals_for(node_id: str) -> dict | None:
    f = _load_feature(node_id)
    if f is None or f.get("node_id") != node_id:
        return None
    return {
        "node_id": f["node_id"],
        "timestamp_ms": int(f.get("timestamp_ms",
                                  int(time.time() * 1000))),
        "presence": bool(f.get("presence", False)),
        "n_persons": int(f.get("n_persons", 0)),
        "confidence": float(f.get("confidence", 0.0)),
        "breathing_rate_bpm": f.get("breathing_rate_bpm"),
        "heartrate_bpm": f.get("heartrate_bpm"),
        "motion": float(f.get("motion", 0.0)),
    }


def bfld_scan_for(node_id: str) -> dict | None:
    f = _load_feature(node_id)
    if f is None or f.get("node_id") != node_id:
        return None
    # ADR-125 §2.1.d: identity_risk_score never crosses the HAP
    # boundary. We mirror that here — even though rvagent's schema
    # has a nullable identity_risk_score slot, we deliberately
    # always return None for it on this bridge.
    return {
        "node_id": f["node_id"],
        "identity_risk_score": None,        # ADR-125 §2.1.d invariant
        "privacy_class": int(f.get("privacy_class", 2)),
        "person_count": int(f.get("n_persons", 0)),
        "confidence": float(f.get("confidence", 0.0)),
        "presence": bool(f.get("presence", False)),
        # timestamp_ns matches BFLD wire format (BfldEvent.timestamp_ns)
        "timestamp_ns": int(f.get("ts", time.time()) * 1_000_000_000),
    }


_PATH_VITALS = re.compile(r"^/api/v1/vitals/([^/]+)/latest$")
_PATH_BFLD_SCAN = re.compile(r"^/api/v1/bfld/([^/]+)/last_scan$")
_PATH_BFLD_SUBSCRIBE = re.compile(r"^/api/v1/bfld/([^/]+)/subscribe$")
_PATH_SEMANTIC = re.compile(r"^/api/v1/semantic-events/([^/]+)/latest$")


def semantic_events_for(node_id: str) -> dict | None:
    """ADR-125 §2.1.d semantic-event surface.

    The three named events that cross the HAP boundary. Each one is a
    boolean + last-fire timestamp. Agents subscribe to this endpoint
    rather than reasoning over raw scores — the naming is the contract.
    """
    f = _load_feature(node_id)
    if f is None or f.get("node_id") != node_id:
        return None
    presence = bool(f.get("presence", False))
    anomaly = float(f.get("anomaly_score") or 0.0)
    return {
        "node_id": f["node_id"],
        "privacy_class": int(f.get("privacy_class", 2)),
        "events": {
            "unknown_presence": {
                "active": presence,
                "source": "BFLD presence_score (rolling 3s avg ≥ 0.30)",
                "ts": f["ts"],
            },
            "unexpected_occupancy": {
                # Placeholder: schedule-aware gating is future work.
                # For now we surface raw occupancy and mark the gate
                # as `schedule_aware=False` so agents know not to
                # equate this with the full §2.1.d intent yet.
                "active": presence,
                "schedule_aware": False,
                "ts": f["ts"],
            },
            "unrecognized_activity_pattern": {
                "active": anomaly >= 0.7,
                "anomaly_threshold": 0.7,
                "anomaly_score": anomaly,
                "ts": f["ts"],
            },
        },
        # ADR-125 §2.1.d invariant restated at the HTTP boundary:
        # identity_risk_score, soul_match_probability, and rf_signature_hash
        # are NEVER published from this endpoint.
        "redacted_fields": [
            "identity_risk_score",
            "soul_match_probability",
            "rf_signature_hash",
        ],
    }


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args) -> None:
        # Quiet the default per-request log; print on a single line.
        sys.stdout.write(
            f"[{self.log_date_time_string()}] {self.command} "
            f"{self.path} -> {args[1] if len(args) > 1 else '?'}\n"
        )

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _asset(self, path: str) -> bool:
        """Serve the checked-in dashboard without exposing files outside ui/."""
        relative = "index.html" if path in ("/", "/ui", "/ui/") else path.removeprefix("/ui/").lstrip("/")
        try:
            candidate = (UI_ROOT / relative).resolve()
            candidate.relative_to(UI_ROOT.resolve())
        except ValueError:
            self._json(404, {"error": "not found"})
            return True
        if not candidate.is_file():
            return False
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".wasm": "application/wasm"}
        payload = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(candidate.suffix.lower(), "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/health", "/health/live", "/health/ready"):
            f = _load_feature()
            self._json(200, {
                "ok": True,
                "feature_age_s": (None if f is None
                                  else round(time.time() - f["ts"], 2)),
                "source": FEATURE_FILE,
            })
            return

        if path == "/room":
            # The app router uses a hash route. Keep this convenient URL valid.
            self.send_response(302)
            self.send_header("Location", "/#room-map")
            self.end_headers()
            return

        if path == "/api/v1/edge/registry":
            self._json(200, {"nodes": _active_nodes()})
            return

        if path == "/movement":
            self.send_response(302)
            self.send_header("Location", "/ui/movement.html")
            self.end_headers()
            return

        if path == "/api/v1/movement/live":
            self._json(200, movement_lab.live())
            return

        if path == "/api/v1/movement/learning":
            self._json(200, movement_lab.movement_learning.get_status(movement_lab.load_baseline()))
            return

        if path == "/api/v1/sensing/latest":
            f = _load_feature()
            wiz = _load_wiz()
            signal = _load_signal()
            baseline = load_baseline()
            if f is None and wiz is None:
                self._json(503, {"error": "no recent feature_state",
                                 "hint": "ESP32 and WiZ listeners have no recent data"})
                return
            if f is None:
                f = {
                    "node_id": "wiz-room",
                    "timestamp_ms": int(time.time() * 1000),
                    "presence": False, "n_persons": 0, "confidence": 0.0,
                    "motion": 0.0, "presence_score": None,
                    "breathing_rate_bpm": None, "heartrate_bpm": None,
                    "privacy_class": 2,
                }
            # ADR-102 sensing/latest schema v2 — the rvagent
            # csi-latest tool ingests this shape.
            esp_online = f["node_id"] != "wiz-room"
            esp_present = bool(f["presence"]) if esp_online else False
            activity = room_activity(signal if esp_online else None, wiz, baseline)
            occupancy_known = activity["active"] is not None
            fused_presence = activity["active"]
            self._json(200, {
                "schema_version": 2,
                "node_id": f["node_id"],
                "timestamp_ms": f["timestamp_ms"],
                "presence": bool(fused_presence),
                "esp32_presence": esp_present,
                "esp32_online": esp_online,
                "n_persons": f["n_persons"],
                "confidence": f["confidence"],
                "motion": f["motion"],
                "presence_score": f.get("presence_score"),
                "breathing_rate_bpm": f.get("breathing_rate_bpm"),
                "heartrate_bpm": f.get("heartrate_bpm"),
                "privacy_class": f.get("privacy_class", 2),
                "wiz": wiz,
                "signal": signal if esp_online else None,
                "baseline": baseline,
                "activity": activity,
                "fusion": {
                    "presence": fused_presence,
                    "occupancy_known": occupancy_known,
                    "sources_online": len(activity["sources"]),
                    "bulbs_online": wiz.get("online_count", 0) if wiz else 0,
                    "method": "Calibrated CSI change OR recent WiZ occupancy event; no position estimate",
                },
            })
            return

        m = _PATH_VITALS.match(path)
        if m:
            node_id = m.group(1)
            v = vitals_for(node_id)
            if v is None:
                self._json(503, {"error": f"no recent vitals for {node_id}",
                                 "hint": "watcher running? node_id correct?"})
                return
            self._json(200, v)
            return

        m = _PATH_BFLD_SCAN.match(path)
        if m:
            node_id = m.group(1)
            r = bfld_scan_for(node_id)
            if r is None:
                self._json(503, {"error": f"no recent BFLD scan for {node_id}",
                                 "hint": "watcher running? node_id correct?"})
                return
            self._json(200, r)
            return

        m = _PATH_SEMANTIC.match(path)
        if m:
            node_id = m.group(1)
            r = semantic_events_for(node_id)
            if r is None:
                self._json(503, {"error": f"no recent semantic events for {node_id}",
                                 "hint": "watcher running? node_id correct?"})
                return
            self._json(200, r)
            return

        if self._asset(path):
            return
        self._json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/api/v1/movement/trials", "/api/v1/movement/trials/exclude"):
            host = self.headers.get("Host", "").split(":", 1)[0].lower()
            origin = self.headers.get("Origin", "")
            if self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("localhost", "127.0.0.1") or (origin and origin not in ("http://localhost:3000", "http://127.0.0.1:3000")):
                self._json(403, {"error": "Calibration must be submitted locally"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 32768:
                    raise ValueError("Invalid body size")
                data = json.loads(self.rfile.read(length))
                legacy = movement_lab.load_baseline()
                learner = movement_lab.movement_learning
                result = (learner.exclude_trial(data.get("id"), legacy) if parsed.path.endswith("/exclude")
                          else learner.add_trial(data, legacy))
                self._json(200, result)
            except (ValueError, AttributeError, TypeError, KeyError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/v1/movement/calibrate":
            host = self.headers.get("Host", "").split(":", 1)[0].lower()
            origin = self.headers.get("Origin", "")
            if self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("localhost", "127.0.0.1") or (origin and origin not in
                    ("http://localhost:3000", "http://127.0.0.1:3000")):
                self._json(403, {"error": "Calibration must be submitted locally"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 16384:
                    raise ValueError("Invalid body size")
                data = json.loads(self.rfile.read(length))
                self._json(200, movement_lab.calibrate(data.get("samples")))
            except (ValueError, AttributeError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/v1/movement/profile":
            host = self.headers.get("Host", "").split(":", 1)[0].lower()
            origin = self.headers.get("Origin", "")
            if self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("localhost", "127.0.0.1") or (origin and origin not in ("http://localhost:3000", "http://127.0.0.1:3000")):
                self._json(403, {"error": "Calibration must be submitted locally"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 32768:
                    raise ValueError("Invalid body size")
                data = json.loads(self.rfile.read(length))
                self._json(200, movement_lab.calibrate_profile(data.get("trials")))
            except (ValueError, AttributeError, TypeError, KeyError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/v1/room/baseline":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 16384:
                    raise ValueError("Invalid body size")
                data = json.loads(self.rfile.read(length))
                if _load_signal() is None:
                    raise ValueError("Live CSI is required")
                self._json(200, save_baseline(data.get("samples")))
            except (ValueError, AttributeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        m = _PATH_BFLD_SUBSCRIBE.match(parsed.path)
        if m:
            qs = parse_qs(parsed.query)
            duration_s = float(qs.get("duration_s", ["10"])[0])
            sub_id = f"sub-{int(time.time() * 1000)}-{m.group(1)}"
            self._json(200, {
                "subscription_id": sub_id,
                "node_id": m.group(1),
                "duration_s": duration_s,
                "endpoint_hint": (f"poll GET /api/v1/bfld/{m.group(1)}"
                                  "/last_scan every 1 s for the window"),
            })
            return
        self._json(404, {"error": "not found", "path": parsed.path})


def main() -> int:
    port = DEFAULT_PORT
    # Browsers keep HTTP connections open while the dashboard is active. A
    # threaded server prevents one stale tab from blocking every new load.
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[sensing-server] listening on 0.0.0.0:{port}", flush=True)
    print(f"[sensing-server] feature source: {FEATURE_FILE}", flush=True)
    print(f"[sensing-server] staleness limit: {STALENESS_S} s", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
