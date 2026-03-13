"""
sensors/state.py — Tapeinos Sensor Registry
============================================
Persists sensor instances to /tmp/tapeinos_sensors.json.
Each sensor record::

    {
        "id":        "us_1",          # unique string id
        "type":      "ultrasonic",    # "ultrasonic" | "camera"
        "name":      "Front Sensor",  # user-supplied label
        "port":      "/dev/ttyUSB0",  # serial port (ultrasonic) or device index (camera)
        "baudrate":  115200,          # ultrasonic only
        "was_running": true,          # True if running at last save — used for auto-reconnect
        "threshold": 20.0,            # stopping sensor threshold, null if not set
        "created_at": 1710000000.0
    }
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


# ---------------------------------------------------------------------------
# Low-level read / write
# ---------------------------------------------------------------------------

def _read() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sensors": {}}


def _write(data: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all() -> dict[str, dict]:
    """Return all sensor records keyed by sensor id."""
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
) -> dict:
    """
    Create and persist a new sensor record.
    Returns the new record.
    """
    with _lock:
        data = _read()
        sensors = data.setdefault("sensors", {})

        sensor_id = f"{sensor_type[:2]}_{uuid.uuid4().hex[:6]}"
        record = {
            "id":          sensor_id,
            "type":        sensor_type,
            "name":        name,
            "port":        port,
            "baudrate":    baudrate,
            "was_running": False,
            "threshold":   None,
            "created_at":  time.time(),
        }
        sensors[sensor_id] = record
        _write(data)
        return record


def remove_sensor(sensor_id: str) -> bool:
    """Remove a sensor record. Returns True if it existed."""
    with _lock:
        data = _read()
        sensors = data.get("sensors", {})
        if sensor_id not in sensors:
            return False
        del sensors[sensor_id]
        _write(data)
        return True


def update(sensor_id: str, **kwargs) -> Optional[dict]:
    """
    Update arbitrary fields on a sensor record.
    Common kwargs: was_running, threshold, name
    Returns updated record or None if not found.
    """
    with _lock:
        data = _read()
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
