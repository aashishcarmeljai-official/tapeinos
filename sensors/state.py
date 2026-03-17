"""
sensors/state.py — Tapeinos Sensor Registry
============================================
Persists sensor instances to /tmp/tapeinos_sensors.json.

Sensor record schema
--------------------
Common fields (all types):
    id, type, name, port, baudrate, was_running, threshold, created_at

Camera-specific fields:
    color            — detection color: "red"|"green"|"blue"|"yellow"
    camera_index     — device index (mirrors port as int)
    calibrated       — True after camera_params.npz saved
    homography_ready — True after homography.txt saved
    target_z         — pick Z height (m)
    step_size        — incremental move step (m)
    place_offset_x   — place X offset from pick (m)
    place_offset_y   — place Y offset from pick (m)
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Optional

STATE_FILE = os.environ.get(
    "TAPEINOS_SENSORS_STATE",
    "/tmp/tapeinos_sensors.json",
)

_lock = threading.Lock()


# ── Low-level read / write ────────────────────────────────────────────────────

def _read() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sensors": {}}


def _write(data: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Public API ────────────────────────────────────────────────────────────────

def get_all() -> dict[str, dict]:
    with _lock:
        return _read().get("sensors", {})


def get(sensor_id: str) -> Optional[dict]:
    with _lock:
        return _read().get("sensors", {}).get(sensor_id)


def add_sensor(
    sensor_type: str,
    name: str,
    port: str,
    baudrate: int = 115200,
    color: str = "red",
) -> dict:
    with _lock:
        data    = _read()
        sensors = data.setdefault("sensors", {})

        sensor_id = f"{sensor_type[:2]}_{uuid.uuid4().hex[:6]}"

        record: dict = {
            "id":          sensor_id,
            "type":        sensor_type,
            "name":        name,
            "port":        port,
            "baudrate":    baudrate,
            "was_running": False,
            "threshold":   None,
            "created_at":  time.time(),
        }

        if sensor_type == "camera":
            record.update({
                "color":            color,
                "camera_index":     int(port) if str(port).isdigit() else 0,
                "calibrated":       False,
                "homography_ready": False,
                "target_z":         0.18,
                "step_size":        0.05,
                "place_offset_x":   0.08,
                "place_offset_y":   0.08,
            })

        sensors[sensor_id] = record
        _write(data)
        return record


def remove_sensor(sensor_id: str) -> bool:
    with _lock:
        data    = _read()
        sensors = data.get("sensors", {})
        if sensor_id not in sensors:
            return False
        del sensors[sensor_id]
        _write(data)
        return True


def update(sensor_id: str, **kwargs) -> Optional[dict]:
    with _lock:
        data    = _read()
        sensors = data.get("sensors", {})
        if sensor_id not in sensors:
            return None
        sensors[sensor_id].update(kwargs)
        _write(data)
        return sensors[sensor_id]


def set_running(sensor_id: str, running: bool) -> None:
    update(sensor_id, was_running=running)


def set_threshold(sensor_id: str, threshold: Optional[float]) -> None:
    update(sensor_id, threshold=threshold)


# ── Camera-specific helpers ───────────────────────────────────────────────────

def set_color(sensor_id: str, color: str) -> None:
    update(sensor_id, color=color)


def set_calibrated(sensor_id: str, calibrated: bool) -> None:
    update(sensor_id, calibrated=calibrated)


def set_homography_ready(sensor_id: str, ready: bool) -> None:
    update(sensor_id, homography_ready=ready)


def set_tracker_params(
    sensor_id: str,
    target_z:       Optional[float] = None,
    step_size:      Optional[float] = None,
    place_offset_x: Optional[float] = None,
    place_offset_y: Optional[float] = None,
) -> None:
    kwargs = {}
    if target_z       is not None: kwargs["target_z"]       = target_z
    if step_size      is not None: kwargs["step_size"]      = step_size
    if place_offset_x is not None: kwargs["place_offset_x"] = place_offset_x
    if place_offset_y is not None: kwargs["place_offset_y"] = place_offset_y
    if kwargs:
        update(sensor_id, **kwargs)