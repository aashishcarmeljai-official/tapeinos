"""
sensors/routes.py — Tapeinos Sensor API
=======================================
Registered as a Flask Blueprint in app.py::

    from sensors.routes import sensors_bp, init_sensors
    app.register_blueprint(sensors_bp)
    init_sensors(app)

Routes
------
GET  /sensors/ports
GET  /sensors/cameras
GET  /sensors/list
POST /sensors/add
POST /sensors/remove/<sensor_id>
POST /sensors/start/<sensor_id>
POST /sensors/stop/<sensor_id>
POST /sensors/action/<sensor_id>
GET  /sensors/logs/<sensor_id>
"""

from __future__ import annotations

import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

from sensors import state as sensors_state

sensors_bp = Blueprint("sensors", __name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCES_ROOT = PROJECT_ROOT / "resources"

# ── Runner registry ───────────────────────────────────────────────────────────
_runners: dict = {}
_runners_lock  = threading.Lock()

# ── Per-sensor SSE log buffers ────────────────────────────────────────────────
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


# ── Runner factory ────────────────────────────────────────────────────────────

def _stop_topic(sensor_id: str) -> str:
    return f"/tapeinos/ultrasonic/{sensor_id}/stop"


def _make_runner(record: dict):
    sensor_type = record["type"]
    sid         = record["id"]

    if sensor_type == "ultrasonic":
        from sensors.ultrasonic.node import UltrasonicRunner
        runner = UltrasonicRunner(
            sensor_id = sid,
            port      = record["port"],
            baudrate  = record.get("baudrate", 115200),
            log_cb    = _get_log_cb(sid),
        )
        if record.get("threshold") is not None:
            runner._pending_threshold = record["threshold"]
        return runner

    if sensor_type == "camera":
        from sensors.camera.camera_node import CameraRunner
        tracker_params = {
            "target_z":       record.get("target_z",       0.18),
            "step_size":      record.get("step_size",       0.05),
            "place_offset_x": record.get("place_offset_x", 0.08),
            "place_offset_y": record.get("place_offset_y", 0.08),
        }
        return CameraRunner(
            sensor_id      = sid,
            camera_index   = record.get("camera_index", record.get("port", 0)),
            color          = record.get("color", "red"),
            log_cb         = _get_log_cb(sid),
            tracker_params = tracker_params,
        )

    raise ValueError(f"Unknown sensor type: {sensor_type}")


# ── Auto-reconnect ────────────────────────────────────────────────────────────

def _auto_reconnect() -> None:
    records = sensors_state.get_all()
    for sid, record in records.items():
        _ensure_log_buffer(sid)
        if not record.get("was_running", False):
            continue
        _append_sensor_log(sid, "[auto-reconnect] attempting to restart…")
        try:
            runner = _make_runner(record)
            ok     = runner.start()
            if ok:
                with _runners_lock:
                    _runners[sid] = runner
                if record["type"] == "ultrasonic":
                    threshold = record.get("threshold")
                    if threshold is not None:
                        runner.set_stop_threshold(float(threshold))
                    _register_stop_with_jog(sid)
                sensors_state.set_running(sid, True)
                _append_sensor_log(sid, "[auto-reconnect] ✓ reconnected")
            else:
                sensors_state.set_running(sid, False)
                _append_sensor_log(sid, f"[auto-reconnect] ✗ failed: {getattr(runner, 'error', '')}")
        except Exception as exc:
            sensors_state.set_running(sid, False)
            _append_sensor_log(sid, f"[auto-reconnect] ✗ error: {exc}")


def init_sensors(app) -> None:
    RESOURCES_ROOT.mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=_auto_reconnect, daemon=True, name="sensor-reconnect")
    t.start()


def shutdown_all_sensors() -> None:
    with _runners_lock:
        for sid, runner in list(_runners.items()):
            try:
                runner.stop()
            except Exception:
                pass
        _runners.clear()


# ── Stop-guard helpers ────────────────────────────────────────────────────────

def _register_stop_with_jog(sensor_id: str) -> None:
    try:
        import jog_once
        runner = jog_once.get_runner()
        if runner:
            topic = _stop_topic(sensor_id)
            runner.register_stop_topic(topic)
            _append_sensor_log(sensor_id, f"[stop-guard] registered → {topic}")
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


# ── Discovery ─────────────────────────────────────────────────────────────────

@sensors_bp.route("/sensors/ports")
def list_ports():
    from sensors.ultrasonic.node import scan_serial_ports
    return jsonify({"ports": scan_serial_ports()})


@sensors_bp.route("/sensors/cameras")
def list_cameras():
    from sensors.camera.camera_node import scan_cameras
    return jsonify({"cameras": scan_cameras()})


# ── Sensor CRUD ───────────────────────────────────────────────────────────────

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
    body        = request.get_json(force=True) or {}
    sensor_type = body.get("type", "").strip()
    name        = body.get("name", "").strip() or sensor_type.title()
    port        = body.get("port", "").strip()
    baudrate    = int(body.get("baudrate", 115200))
    color       = body.get("color", "red").strip().lower()

    if sensor_type not in ("ultrasonic", "camera"):
        return jsonify({"error": f"unknown type: {sensor_type}"}), 400
    if not port and port != "0":
        return jsonify({"error": "port is required"}), 400

    record = sensors_state.add_sensor(
        sensor_type = sensor_type,
        name        = name,
        port        = port,
        baudrate    = baudrate,
        color       = color,
    )
    _ensure_log_buffer(record["id"])
    _append_sensor_log(record["id"], f"[created] {sensor_type} sensor on {port}")
    return jsonify({"status": "created", "sensor": record})


@sensors_bp.route("/sensors/remove/<sensor_id>", methods=["POST"])
def remove_sensor(sensor_id: str):
    record = sensors_state.get(sensor_id)
    if not record:
        return jsonify({"error": "sensor not found"}), 404

    with _runners_lock:
        runner = _runners.pop(sensor_id, None)
    if runner:
        try:
            runner.stop()
        except Exception:
            pass

    if record.get("type") == "ultrasonic":
        _unregister_stop_from_jog(sensor_id)

    resources_dir = RESOURCES_ROOT / sensor_id
    if resources_dir.exists():
        shutil.rmtree(resources_dir, ignore_errors=True)

    sensors_state.remove_sensor(sensor_id)
    _log_buffers.pop(sensor_id, None)
    _log_locks.pop(sensor_id, None)
    return jsonify({"status": "removed", "sensor_id": sensor_id})


# ── Start / Stop ──────────────────────────────────────────────────────────────

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

        if record["type"] == "ultrasonic":
            threshold = record.get("threshold")
            if threshold is not None:
                runner.set_stop_threshold(float(threshold))
            _register_stop_with_jog(sensor_id)

        sensors_state.set_running(sensor_id, True)
        return jsonify({"status": "started", "sensor_id": sensor_id})
    else:
        return jsonify({"status": "error",
                        "message": getattr(runner, "error", "unknown")}), 500


@sensors_bp.route("/sensors/stop/<sensor_id>", methods=["POST"])
def stop_sensor(sensor_id: str):
    with _runners_lock:
        runner = _runners.pop(sensor_id, None)

    if runner:
        record = sensors_state.get(sensor_id)
        if record and record.get("type") == "ultrasonic":
            _unregister_stop_from_jog(sensor_id)
        runner.stop()
        sensors_state.set_running(sensor_id, False)
        _append_sensor_log(sensor_id, "[stopped]")
        return jsonify({"status": "stopped", "sensor_id": sensor_id})

    return jsonify({"status": "not_running", "sensor_id": sensor_id})


# ── Actions ───────────────────────────────────────────────────────────────────

@sensors_bp.route("/sensors/action/<sensor_id>", methods=["POST"])
def sensor_action(sensor_id: str):
    record = sensors_state.get(sensor_id)
    if not record:
        return jsonify({"error": "sensor not found"}), 404

    body   = request.get_json(force=True) or {}
    action = body.get("action", "").strip()

    with _runners_lock:
        runner = _runners.get(sensor_id)

    # ── Ultrasonic ────────────────────────────────────────────────────────────
    if record["type"] == "ultrasonic":
        if action == "set_threshold":
            try:
                threshold = float(body["threshold"])
            except (KeyError, ValueError):
                return jsonify({"error": "threshold (float) required"}), 400
            if runner:
                runner.set_stop_threshold(threshold)
            sensors_state.set_threshold(sensor_id, threshold)
            _append_sensor_log(sensor_id,
                f"[stopping sensor] threshold updated → {threshold:.1f} cm")
            return jsonify({"status": "ok", "threshold": threshold})

        if action == "clear_threshold":
            if runner:
                runner.clear_stop_threshold()
            sensors_state.set_threshold(sensor_id, None)
            return jsonify({"status": "ok", "threshold": None})

        return jsonify({"error": f"unknown ultrasonic action: {action}"}), 400

    # ── Camera ────────────────────────────────────────────────────────────────
    if record["type"] == "camera":

        # set_color — no runner required, persists immediately
        if action == "set_color":
            color = body.get("color", "red").lower()
            if color not in ("red", "green", "blue", "yellow"):
                return jsonify({"error": "invalid color. Choose: red, green, blue, yellow"}), 400
            if runner:
                runner.set_color(color)
            sensors_state.set_color(sensor_id, color)
            _append_sensor_log(sensor_id, f"[config] color → {color}")
            return jsonify({"status": "ok", "color": color})

        # set_tracker_params — no runner required, persists immediately
        if action == "set_tracker_params":
            keys   = ("target_z", "step_size", "place_offset_x", "place_offset_y")
            params = {}
            for k in keys:
                if k in body:
                    try:
                        params[k] = float(body[k])
                    except (TypeError, ValueError):
                        return jsonify({"error": f"invalid value for {k}"}), 400
            if runner:
                runner.run_action("set_tracker_params", params)
            sensors_state.set_tracker_params(sensor_id, **params)
            _append_sensor_log(sensor_id, f"[config] tracker params → {params}")
            return jsonify({"status": "ok", "params": params})

        # All remaining actions require the runner (cam_pub must be ON)
        if not runner:
            return jsonify({
                "error": "Camera sensor not running. Toggle ON first."
            }), 409

        # track_objects runs in background
        if action == "track_objects":
            def _bg():
                ok, msg = runner.run_action(action, body)
                _append_sensor_log(sensor_id,
                    f"[track_objects] {'✓' if ok else '✗'} {msg}")
            threading.Thread(target=_bg, daemon=True,
                              name=f"cam-track-{sensor_id}").start()
            return jsonify({"status": "started", "action": action})

        # Synchronous actions
        ok, msg = runner.run_action(action, body)

        # Post-action state sync
        if ok:
            if action == "compute_homography":
                sensors_state.update(sensor_id, calibrated=True)
            if action == "convert_homography":
                sensors_state.set_homography_ready(sensor_id, True)
            if action == "collect_homography":
                # return collected count so JS can update counter
                status = runner.get_status()
                return jsonify({
                    "status":           "ok",
                    "message":          msg,
                    "collected_points": status.get("collected_points", 0),
                })

        if ok:
            return jsonify({"status": "ok", "message": msg})
        else:
            return jsonify({"error": msg}), 500

    return jsonify({"error": f"unknown sensor type: {record['type']}"}), 400


# ── SSE log stream ────────────────────────────────────────────────────────────

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
        if len(buf) > last_len:
            for line in buf[last_len:]:
                yield f"data: {line}\n\n"
            last_len = len(buf)
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
