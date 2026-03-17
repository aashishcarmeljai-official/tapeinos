"""
camera_node.py — Tapeinos Camera Runner
========================================
Manages:
  1. cam_pub subprocess        — publishes /video_frames
  2. Script subprocess actions — each action launches its corresponding
                                  .py script with a cv2 window where applicable

Action → Script mapping
-----------------------
  calibrate          → camera_calibration.py   (cv2 window)
  collect_homography → homography_collect.py   (cv2 window)
  compute_homography → compute_homography.py   (terminal only)
  convert_homography → convert_homography.py   (terminal only)
  track_objects      → green_tracker.py        (cv2 window)
  get_intrinsic      → reads camera_params.npz  (terminal only, no subprocess)
  get_extrinsic      → reads camera_params.npz  (terminal only, no subprocess)
  get_distortion     → reads camera_params.npz  (terminal only, no subprocess)
  set_color          → persists to state        (no subprocess)
  set_tracker_params → persists to state        (no subprocess)

File dependency chain
---------------------
  camera_params.npz      <- camera_calibration.py
  homography_points.npz  <- homography_collect.py   (needs camera_params.npz)
  homography.npz         <- compute_homography.py   (needs homography_points.npz)
  homography.txt         <- convert_homography.py   (needs homography.npz)
  green_tracker          <- needs homography.txt
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
CAMERA_DIR       = Path(__file__).resolve().parent
PROJECT_ROOT     = CAMERA_DIR.parents[1]
RESOURCES_ROOT   = PROJECT_ROOT / "resources"
BASH_SOURCE_FILE = str(PROJECT_ROOT / "bash.source")
CAM_PUB_SCRIPT   = str(CAMERA_DIR / "cam_pub.py")

# ── Script paths ──────────────────────────────────────────────────────────────
SCRIPTS = {
    "calibrate":          str(CAMERA_DIR / "camera_calibration.py"),
    "collect_homography": str(CAMERA_DIR / "homography_collect.py"),
    "compute_homography": str(CAMERA_DIR / "compute_homography.py"),
    "convert_homography": str(CAMERA_DIR / "convert_homography.py"),
    "track_objects":      str(CAMERA_DIR / "green_tracker.py"),
}

# ── File dependencies (action -> required file before it can run) ─────────────
REQUIRED_FILES = {
    "collect_homography": "camera_params.npz",
    "compute_homography": "homography_points.npz",
    "convert_homography": "homography.npz",
    "track_objects":      "homography.txt",
    "get_intrinsic":      "camera_params.npz",
    "get_extrinsic":      "camera_params.npz",
    "get_distortion":     "camera_params.npz",
}

MISSING_MSG = {
    "camera_params.npz":     "Run 'Calibrate Camera' first to generate camera_params.npz.",
    "homography_points.npz": "Run 'Collect Homography Points' first.",
    "homography.npz":        "Run 'Compute Homography' first.",
    "homography.txt":        "Run 'Convert Homography' first.",
}


# ─────────────────────────────────────────────────────────────────────────────
# Shell helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ros2_source_prefix() -> str:
    if not os.path.exists(BASH_SOURCE_FILE):
        return ""
    lines = []
    with open(BASH_SOURCE_FILE) as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return " && ".join(lines)


def _wrap_cmd(cmd: list[str]) -> list[str]:
    prefix  = _ros2_source_prefix()
    cmd_str = " ".join(cmd)
    if prefix:
        return ["bash", "--login", "-c", f"{prefix} && {cmd_str}"]
    return cmd


def _build_env() -> dict:
    uid = os.getuid()
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    # X display — preserve existing or fall back to :0
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"

    # X authority — needed when Flask runs outside a desktop session
    if not env.get("XAUTHORITY"):
        for candidate in (
            os.path.expanduser("~/.Xauthority"),
            f"/run/user/{uid}/gdm/Xauthority",
            "/tmp/.Xauthority",
        ):
            if os.path.exists(candidate):
                env["XAUTHORITY"] = candidate
                break

    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    return env


# ─────────────────────────────────────────────────────────────────────────────
# CameraRunner
# ─────────────────────────────────────────────────────────────────────────────

class CameraRunner:
    """
    Manages cam_pub subprocess and launches camera script subprocesses.
    """

    def __init__(
        self,
        sensor_id:      str,
        camera_index:   int | str  = 0,
        color:          str        = "red",
        log_cb:         Callable[[str], None] | None = None,
        tracker_params: dict | None = None,
    ):
        self.sensor_id      = sensor_id
        self.camera_index   = int(camera_index)
        self.color          = color.lower()
        self._log_cb        = log_cb or (lambda x: None)
        self.tracker_params = tracker_params or {}

        self.resources_dir  = RESOURCES_ROOT / sensor_id
        self.resources_dir.mkdir(parents=True, exist_ok=True)

        # cam_pub subprocess
        self._proc:           Optional[subprocess.Popen] = None
        self._proc_lock       = threading.Lock()

        # active action subprocess (one at a time)
        self._action_proc:    Optional[subprocess.Popen] = None
        self._action_lock     = threading.Lock()

        self.running = False
        self.error   = ""

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self._log_cb(msg)

    # ── File helpers ──────────────────────────────────────────────────────────

    def _file(self, name: str) -> Path:
        return self.resources_dir / name

    def _has(self, name: str) -> bool:
        return self._file(name).exists()

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._proc_lock:
            proc_alive = self._proc is not None and self._proc.poll() is None
        with self._action_lock:
            action_running = (self._action_proc is not None and
                              self._action_proc.poll() is None)
        return {
            "running":            proc_alive,
            "action_running":     action_running,
            "color":              self.color,
            "resources_dir":      str(self.resources_dir),
            "calibrated":         self._has("camera_params.npz"),
            "homography_points":  self._has("homography_points.npz"),
            "homography_npz":     self._has("homography.npz"),
            "homography_ready":   self._has("homography.txt"),
        }

    def set_color(self, color: str) -> None:
        self.color = color.lower()

    # ── cam_pub start / stop ──────────────────────────────────────────────────

    def start(self) -> bool:
        with self._proc_lock:
            if self._proc and self._proc.poll() is None:
                self._log("[warn] cam_pub already running")
                self.running = True
                return True

        cmd     = ["python3", CAM_PUB_SCRIPT, "--camera-index", str(self.camera_index)]
        wrapped = _wrap_cmd(cmd)

        try:
            proc = subprocess.Popen(
                wrapped,
                stdout            = subprocess.PIPE,
                stderr            = subprocess.STDOUT,
                stdin             = subprocess.DEVNULL,
                env               = _build_env(),
                start_new_session = True,
            )
            with self._proc_lock:
                self._proc = proc

            self._log(f"[started] cam_pub PID {proc.pid} — camera index {self.camera_index}")

            threading.Thread(
                target = self._stream_proc,
                args   = (proc, "[cam_pub]"),
                daemon = True,
                name   = f"cam-reader-{self.sensor_id}",
            ).start()

            self.running = True
            self.error   = ""
            return True

        except Exception as exc:
            self.error   = str(exc)
            self.running = False
            self._log(f"[error] {exc}")
            return False

    def stop(self) -> None:
        self._kill_action()

        with self._proc_lock:
            proc       = self._proc
            self._proc = None

        self._kill_proc(proc)
        self.running = False
        self._log("[stopped] cam_pub stopped")

    # ── Process helpers ───────────────────────────────────────────────────────

    def _kill_proc(self, proc: Optional[subprocess.Popen]) -> None:
        if not proc or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            for _ in range(30):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    def _kill_action(self) -> None:
        with self._action_lock:
            proc              = self._action_proc
            self._action_proc = None
        self._kill_proc(proc)

    def _stream_proc(self, proc: subprocess.Popen, prefix: str = "") -> None:
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                self._log(f"{prefix} {line}".strip() if prefix else line)
        except Exception as exc:
            self._log(f"[reader error] {exc}")
        finally:
            rc = proc.wait()
            self._log(f"{prefix} [exited rc={rc}]".strip())
            # if cam_pub exited, mark not running
            with self._proc_lock:
                if proc is self._proc:
                    self.running = False

    # ── Action subprocess launcher ────────────────────────────────────────────

    def _launch_script(self, action: str, extra_args: list[str]) -> tuple[bool, str]:
        script = SCRIPTS.get(action)
        if not script:
            return False, f"No script registered for action '{action}'"
        if not os.path.exists(script):
            return False, f"Script not found: {script}"

        # Kill any existing action
        self._kill_action()

        env = _build_env()
        self._log(f"[{action}] DISPLAY={env.get('DISPLAY')}  "
                  f"XAUTHORITY={env.get('XAUTHORITY','(none)')}")

        cmd     = ["python3", script, "--sensor-id", self.sensor_id] + extra_args
        wrapped = _wrap_cmd(cmd)

        try:
            proc = subprocess.Popen(
                wrapped,
                stdout            = subprocess.PIPE,
                stderr            = subprocess.STDOUT,
                stdin             = subprocess.DEVNULL,
                env               = env,
                start_new_session = True,
            )
            with self._action_lock:
                self._action_proc = proc

            self._log(f"[{action}] started PID {proc.pid}")

            threading.Thread(
                target = self._stream_proc,
                args   = (proc, f"[{action}]"),
                daemon = True,
                name   = f"action-{self.sensor_id}-{action}",
            ).start()

            return True, f"{action} started (PID {proc.pid})"

        except Exception as exc:
            self._log(f"[error] launching {action}: {exc}")
            return False, str(exc)

    # ── Action dispatcher ─────────────────────────────────────────────────────

    def run_action(self, action: str, params: dict) -> tuple[bool, str]:

        # Config actions — no subprocess, no dependency check
        if action == "set_color":
            color = params.get("color", "red").lower()
            if color not in ("red", "green", "blue", "yellow"):
                return False, f"invalid color '{color}'"
            self.color = color
            self._log(f"[config] detection color → {color}")
            return True, f"color set to {color}"

        if action == "set_tracker_params":
            for k in ("target_z", "step_size", "place_offset_x", "place_offset_y"):
                if k in params:
                    try:
                        self.tracker_params[k] = float(params[k])
                    except (TypeError, ValueError):
                        return False, f"invalid value for {k}"
            self._log(f"[config] tracker params → {self.tracker_params}")
            return True, "tracker params updated"

        if action == "stop_action":
            self._kill_action()
            self._log("[action] stopped")
            return True, "action stopped"

        # File-read actions — no subprocess
        if action in ("get_intrinsic", "get_extrinsic", "get_distortion"):
            return self._read_calibration(action)

        # Dependency check before any subprocess launch
        required = REQUIRED_FILES.get(action)
        if required and not self._has(required):
            msg = MISSING_MSG.get(required, f"{required} missing.")
            self._log(f"[{action}] ⚠ {msg}")
            return False, msg

        # Script launchers
        if action == "calibrate":
            return self._launch_script(action, [])

        if action == "collect_homography":
            return self._launch_script(action, ["--color", self.color])

        if action == "compute_homography":
            return self._launch_script(action, [])

        if action == "convert_homography":
            return self._launch_script(action, [])

        if action == "track_objects":
            tp = self.tracker_params
            extra = [
                "--color",          self.color,
                "--target-z",       str(tp.get("target_z",       0.18)),
                "--step-size",      str(tp.get("step_size",       0.05)),
                "--place-offset-x", str(tp.get("place_offset_x", 0.08)),
                "--place-offset-y", str(tp.get("place_offset_y", 0.08)),
            ]
            return self._launch_script(action, extra)

        return False, f"unknown action: {action}"

    # ── Calibration file readers ──────────────────────────────────────────────

    def _read_calibration(self, action: str) -> tuple[bool, str]:
        import numpy as np
        path = self._file("camera_params.npz")
        if not path.exists():
            return False, "camera_params.npz not found. Run 'Calibrate Camera' first."
        try:
            cal = np.load(str(path))
        except Exception as exc:
            return False, f"failed to load camera_params.npz: {exc}"

        if action == "get_intrinsic":
            K   = cal["K"]
            rms = float(cal["rms"][0]) if "rms" in cal else float("nan")
            self._log("─" * 48)
            self._log("[intrinsic] Camera Matrix K:")
            self._log(f"  [ {K[0,0]:.4f}  {K[0,1]:.4f}  {K[0,2]:.4f} ]")
            self._log(f"  [ {K[1,0]:.4f}  {K[1,1]:.4f}  {K[1,2]:.4f} ]")
            self._log(f"  [ {K[2,0]:.4f}  {K[2,1]:.4f}  {K[2,2]:.4f} ]")
            self._log(f"  fx={K[0,0]:.2f}  fy={K[1,1]:.2f}  "
                      f"cx={K[0,2]:.2f}  cy={K[1,2]:.2f}")
            self._log(f"  RMS = {rms:.4f} px")
            self._log("─" * 48)
            return True, "intrinsic logged"

        if action == "get_extrinsic":
            rvecs = cal.get("rvecs")
            tvecs = cal.get("tvecs")
            if rvecs is None:
                return False, "rvecs/tvecs not saved. Re-run calibration."
            self._log("─" * 48)
            self._log(f"[extrinsic] {len(rvecs)} poses:")
            for i, (rv, tv) in enumerate(zip(rvecs, tvecs)):
                rv = rv.flatten(); tv = tv.flatten()
                self._log(f"  {i+1:02d}: rvec=({rv[0]:.4f},{rv[1]:.4f},{rv[2]:.4f})"
                          f"  tvec=({tv[0]:.4f},{tv[1]:.4f},{tv[2]:.4f})")
            self._log("─" * 48)
            return True, f"{len(rvecs)} poses logged"

        if action == "get_distortion":
            D = cal["D"].flatten()
            self._log("─" * 48)
            self._log("[distortion] Coefficients:")
            for lbl, val in zip(["k1", "k2", "p1", "p2", "k3"], D):
                self._log(f"  {lbl} = {val:.6f}")
            self._log("─" * 48)
            return True, "distortion logged"

        return False, f"unknown read action: {action}"


# ── scan_cameras ──────────────────────────────────────────────────────────────

def scan_cameras() -> list[dict]:
    available = []
    try:
        import cv2
        for i in range(8):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    available.append({"index": i, "label": f"Camera {i} (/dev/video{i})"})
            cap.release()
    except Exception:
        pass
    return available
