"""
sensor_routes.py — Tapeinos Sensor API
=======================================
Registered as a Flask Blueprint in app.py::

    from sensor_routes import sensors_bp, init_sensors
    app.register_blueprint(sensors_bp)
    init_sensors(app)          # call after app is created — starts auto-reconnect

Routes
------
GET  /sensors/ports                   — scan serial ports
GET  /sensors/cameras                 — scan camera devices
GET  /sensors/list                    — all persisted sensor records + live status
POST /sensors/add                     — add a new sensor instance
POST /sensors/remove/<sensor_id>      — remove sensor instance
POST /sensors/start/<sensor_id>       — start ROS2 node
POST /sensors/stop/<sensor_id>        — stop ROS2 node
POST /sensors/action/<sensor_id>      — perform a sensor action
GET  /sensors/logs/<sensor_id>        — SSE log stream
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

import sensors_state

sensors_bp = Blueprint("sensors", __name__)

# ---------------------------------------------------------------------------
# In-memory runner registry  { sensor_id -> UltrasonicRunner | CameraRunner }
# ---------------------------------------------------------------------------
_runners: dict = {}
_runners_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Per-sensor log buffers  (same pattern as main panel log_buffers)
# ---------------------------------------------------------------------------
LOG_HISTORY = 200
_log_buffers: dict[str, deque] = {}
_log_locks:   dict[str, threading.Lock] = {}


def _ensure_log_buffer(sensor_id: str) -> None:
    if sensor_id not in _log_buffers:
        _log_buffers[sensor_id] = deque(maxlen=LOG_HISTORY)
        _log_locks[sensor_id]   = threading.Lock()


def _append_sensor_log(sensor_id: str, line: str) -> None:
    _ensure_log_buffer(sensor_id)
    with _log_locks[sensor_id]:
        _log_buffers[sensor_id].append(line)


def _get_log_cb(sensor_id: str):
    return lambda line: _append_sensor_log(sensor_id, line)


# ---------------------------------------------------------------------------
# Runner factory
# ---------------------------------------------------------------------------

def _stop_topic(sensor_id: str) -> str:
    return f"/tapeinos/ultrasonic/{sensor_id}/stop"


def _make_runner(record: dict):
    sensor_type = record["type"]
    sid         = record["id"]

    if sensor_type == "ultrasonic":
        from ultrasonic_node import UltrasonicRunner
        runner = UltrasonicRunner(
            sensor_id = sid,
            port      = record["port"],
            baudrate  = record.get("baudrate", 115200),
            log_cb    = _get_log_cb(sid),
        )
        # Restore threshold if previously set
        if record.get("threshold") is not None:
            runner._pending_threshold = record["threshold"]
        return runner

    if sensor_type == "camera":
        # Camera runner will be added once camera_node.py is implemented
        raise NotImplementedError("Camera runner not yet implemented")

    raise ValueError(f"Unknown sensor type: {sensor_type}")


# ---------------------------------------------------------------------------
# Auto-reconnect — called once at app startup
# ---------------------------------------------------------------------------

def _auto_reconnect() -> None:
    """
    Re-start any sensor that was running when the app last shut down.
    Runs in a background thread so it doesn't block Flask startup.
    """
    records = sensors_state.get_all()
    for sid, record in records.items():
        _ensure_log_buffer(sid)
        if not record.get("was_running", False):
            continue
        _append_sensor_log(sid, "[auto-reconnect] attempting to restart…")
        try:
            runner = _make_runner(record)
            ok = runner.start()
            if ok:
                with _runners_lock:
                    _runners[sid] = runner
                # Restore threshold after start
                threshold = record.get("threshold")
                if threshold is not None:
                    runner.set_stop_threshold(float(threshold))
                sensors_state.set_running(sid, True)
                if record["type"] == "ultrasonic":
                    _register_stop_with_jog(sid)
                _append_sensor_log(sid, "[auto-reconnect] ✓ reconnected")
            else:
                sensors_state.set_running(sid, False)
                _append_sensor_log(sid, f"[auto-reconnect] ✗ failed: {runner.error}")
        except Exception as exc:
            sensors_state.set_running(sid, False)
            _append_sensor_log(sid, f"[auto-reconnect] ✗ error: {exc}")


def init_sensors(app) -> None:
    """Call after app.register_blueprint — starts auto-reconnect thread."""
    t = threading.Thread(target=_auto_reconnect, daemon=True, name="sensor-reconnect")
    t.start()


# ---------------------------------------------------------------------------
# Shutdown — called from app.py _shutdown_all
# ---------------------------------------------------------------------------

def shutdown_all_sensors() -> None:
    with _runners_lock:
        for sid, runner in list(_runners.items()):
            try:
                runner.stop()
            except Exception:
                pass
        _runners.clear()


# ---------------------------------------------------------------------------
# Stop-guard helpers
# ---------------------------------------------------------------------------

def _register_stop_with_jog(sensor_id: str) -> None:
    try:
        import jog_once
        runner = jog_once.get_runner()
        if runner:
            topic = _stop_topic(sensor_id)
            runner.register_stop_topic(topic)
            _append_sensor_log(sensor_id, f"[stop-guard] registered with JogRunner → {topic}")
    except Exception as exc:
        _append_sensor_log(sensor_id, f"[warn] could not register stop topic: {exc}")


def _unregister_stop_from_jog(sensor_id: str) -> None:
    try:
        import jog_once
        runner = jog_once.get_runner()
        if runner:
            runner.unregister_stop_topic(_stop_topic(sensor_id))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Routes — Discovery
# ---------------------------------------------------------------------------

@sensors_bp.route("/sensors/ports")
def list_ports():
    from ultrasonic_node import scan_serial_ports
    return jsonify({"ports": scan_serial_ports()})


@sensors_bp.route("/sensors/cameras")
def list_cameras():
    """Scan for available OpenCV camera indices."""
    available = []
    try:
        import cv2
        for i in range(8):   # check indices 0-7
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    available.append({"index": i, "label": f"Camera {i} (/dev/video{i})"})
                cap.release()
    except ImportError:
        return jsonify({"error": "opencv-python not installed", "cameras": []})
    except Exception as exc:
        return jsonify({"error": str(exc), "cameras": []})
    return jsonify({"cameras": available})


# ---------------------------------------------------------------------------
# Routes — Sensor CRUD
# ---------------------------------------------------------------------------

@sensors_bp.route("/sensors/list")
def list_sensors():
    records = sensors_state.get_all()
    result  = []
    for sid, rec in records.items():
        with _runners_lock:
            runner = _runners.get(sid)
        status = runner.get_status() if runner and hasattr(runner, "get_status") else {}
        result.append({**rec, "live": status})
    return jsonify({"sensors": result})


@sensors_bp.route("/sensors/add", methods=["POST"])
def add_sensor():
    body = request.get_json(force=True) or {}
    sensor_type = body.get("type", "").strip()
    name        = body.get("name", "").strip() or sensor_type.title()
    port        = body.get("port", "").strip()
    baudrate    = int(body.get("baudrate", 115200))

    if sensor_type not in ("ultrasonic", "camera"):
        return jsonify({"error": f"unknown type: {sensor_type}"}), 400
    if not port:
        return jsonify({"error": "port is required"}), 400

    record = sensors_state.add_sensor(
        sensor_type = sensor_type,
        name        = name,
        port        = port,
        baudrate    = baudrate,
    )
    _ensure_log_buffer(record["id"])
    _append_sensor_log(record["id"], f"[created] {sensor_type} sensor on {port}")
    return jsonify({"status": "created", "sensor": record})


@sensors_bp.route("/sensors/remove/<sensor_id>", methods=["POST"])
def remove_sensor(sensor_id: str):
    # Stop runner if active
    with _runners_lock:
        runner = _runners.pop(sensor_id, None)
    if runner:
        try:
            runner.stop()
        except Exception:
            pass

    removed = sensors_state.remove_sensor(sensor_id)
    if not removed:
        return jsonify({"error": "sensor not found"}), 404

    # Unregister stop topic if it was an ultrasonic sensor
    rec = sensors_state.get(sensor_id)  # already deleted but we have type from runner
    _unregister_stop_from_jog(sensor_id)  # safe even if not registered
    # Clean up log buffer
    _log_buffers.pop(sensor_id, None)
    _log_locks.pop(sensor_id, None)

    return jsonify({"status": "removed", "sensor_id": sensor_id})


# ---------------------------------------------------------------------------
# Routes — Start / Stop
# ---------------------------------------------------------------------------

@sensors_bp.route("/sensors/start/<sensor_id>", methods=["POST"])
def start_sensor(sensor_id: str):
    record = sensors_state.get(sensor_id)
    if not record:
        return jsonify({"error": "sensor not found"}), 404

    with _runners_lock:
        existing = _runners.get(sensor_id)
        if existing and existing.running:
            return jsonify({"status": "already_running", "sensor_id": sensor_id})

    _ensure_log_buffer(sensor_id)
    try:
        runner = _make_runner(record)
    except NotImplementedError as exc:
        return jsonify({"error": str(exc)}), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    ok = runner.start()
    if ok:
        with _runners_lock:
            _runners[sensor_id] = runner
        # Restore threshold
        threshold = record.get("threshold")
        if threshold is not None:
            runner.set_stop_threshold(float(threshold))
        sensors_state.set_running(sensor_id, True)
        # Wire stop signal into JogRunner if it exists
        if record["type"] == "ultrasonic":
            _register_stop_with_jog(sensor_id)
        return jsonify({"status": "started", "sensor_id": sensor_id})
    else:
        return jsonify({"status": "error", "message": runner.error}), 500


@sensors_bp.route("/sensors/stop/<sensor_id>", methods=["POST"])
def stop_sensor(sensor_id: str):
    with _runners_lock:
        runner = _runners.pop(sensor_id, None)

    if runner:
        # Remove stop-guard from JogRunner before shutting down node
        record = sensors_state.get(sensor_id)
        if record and record["type"] == "ultrasonic":
            _unregister_stop_from_jog(sensor_id)
        runner.stop()
        sensors_state.set_running(sensor_id, False)
        _append_sensor_log(sensor_id, "[stopped]")
        return jsonify({"status": "stopped", "sensor_id": sensor_id})

    return jsonify({"status": "not_running", "sensor_id": sensor_id})


# ---------------------------------------------------------------------------
# Routes — Actions
# ---------------------------------------------------------------------------

@sensors_bp.route("/sensors/action/<sensor_id>", methods=["POST"])
def sensor_action(sensor_id: str):
    record = sensors_state.get(sensor_id)
    if not record:
        return jsonify({"error": "sensor not found"}), 404

    body   = request.get_json(force=True) or {}
    action = body.get("action", "").strip()

    with _runners_lock:
        runner = _runners.get(sensor_id)

    # ── Ultrasonic actions ───────────────────────────────────────────
    if record["type"] == "ultrasonic":

        if action == "set_threshold":
            try:
                threshold = float(body["threshold"])
            except (KeyError, ValueError):
                return jsonify({"error": "threshold (float) required"}), 400

            if runner:
                runner.set_stop_threshold(threshold)
            sensors_state.set_threshold(sensor_id, threshold)
            _append_sensor_log(
                sensor_id,
                f"[stopping sensor] threshold updated → {threshold:.1f} cm"
            )
            return jsonify({"status": "ok", "threshold": threshold})

        if action == "clear_threshold":
            if runner:
                runner.clear_stop_threshold()
            sensors_state.set_threshold(sensor_id, None)
            return jsonify({"status": "ok", "threshold": None})

        return jsonify({"error": f"unknown ultrasonic action: {action}"}), 400

    # ── Camera actions ───────────────────────────────────────────────
    if record["type"] == "camera":
        # Implemented once camera_node.py is ready
        return jsonify({"error": "camera actions not yet implemented"}), 501

    return jsonify({"error": f"unknown sensor type: {record['type']}"}), 400


# ---------------------------------------------------------------------------
# Routes — SSE log stream
# ---------------------------------------------------------------------------

def _sse_sensor_log_generator(sensor_id: str):
    _ensure_log_buffer(sensor_id)

    with _log_locks[sensor_id]:
        history = list(_log_buffers[sensor_id])
    for line in history:
        yield f"data: {line}\n\n"

    last_len = len(history)
    while True:
        time.sleep(0.25)
        with _log_locks[sensor_id]:
            buf = list(_log_buffers[sensor_id])
        current_len = len(buf)
        if current_len > last_len:
            for line in buf[last_len:]:
                yield f"data: {line}\n\n"
            last_len = current_len
        else:
            yield ": ping\n\n"


@sensors_bp.route("/sensors/logs/<sensor_id>")
def sensor_log_stream(sensor_id: str):
    if not sensors_state.get(sensor_id):
        return jsonify({"error": "sensor not found"}), 404
    return Response(
        stream_with_context(_sse_sensor_log_generator(sensor_id)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )