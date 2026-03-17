"""
green_tracker.py — Launcher for C++ green_tracker_moveit

Uses a pre-captured clean terminal environment (clean_env.json) stored in
the same directory as this script, so that Snap's libpthread is never loaded.

To regenerate clean_env.json (run from a normal terminal):
    python3 -c "import os, json; print(json.dumps(dict(os.environ)))" > clean_env.json
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CAMERA_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT   = CAMERA_DIR.parents[1]
RESOURCES_ROOT = PROJECT_ROOT / "resources"
BASH_SOURCE    = PROJECT_ROOT / "bash.source"
CLEAN_ENV_PATH = CAMERA_DIR / "clean_env.json"   # <-- lives in same dir as this script

# System library paths — always prepended to guarantee system libpthread wins
SYSTEM_LIB_PATHS = [
    "/usr/lib/x86_64-linux-gnu",
    "/lib/x86_64-linux-gnu",
    "/usr/lib",
    "/lib",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_env() -> dict:
    """
    Build a clean environment dict from clean_env.json (captured from a working
    terminal session).  Falls back to os.environ if the file is missing, with
    a loud warning.

    Either way:
      • Snap variables and LD_PRELOAD are removed.
      • /snap/* entries are stripped from LD_LIBRARY_PATH.
      • System library paths are prepended.
    """
    uid = os.getuid()

    # -- Load base environment -----------------------------------------
    if CLEAN_ENV_PATH.exists():
        with open(CLEAN_ENV_PATH) as f:
            env = json.load(f)
        print(f"[tracker] ✔ loaded clean env from: {CLEAN_ENV_PATH}", flush=True)
    else:
        env = dict(os.environ)
        print(
            f"[tracker] ⚠  clean_env.json not found at {CLEAN_ENV_PATH}\n"
            f"[tracker]    Run this in a working terminal to create it:\n"
            f"[tracker]    python3 -c \"import os,json; print(json.dumps(dict(os.environ)))\" "
            f"> {CLEAN_ENV_PATH}",
            flush=True,
        )

    # -- Strip Snap ----------------------------------------------------
    for snap_var in (
        "SNAP",
        "SNAP_NAME",
        "SNAP_REVISION",
        "SNAP_ARCH",
        "SNAP_LIBRARY_PATH",
        "LD_PRELOAD",
    ):
        env.pop(snap_var, None)

    # -- Sanitise LD_LIBRARY_PATH (remove any residual /snap/* entries) -
    raw_ldpath = env.get("LD_LIBRARY_PATH", "")
    filtered = [p for p in raw_ldpath.split(":") if p and "/snap/" not in p]
    env["LD_LIBRARY_PATH"] = ":".join(SYSTEM_LIB_PATHS + filtered)

    print(f"[tracker] LD_LIBRARY_PATH = {env['LD_LIBRARY_PATH']}", flush=True)

    # -- Display / runtime helpers -------------------------------------
    env["PYTHONUNBUFFERED"] = "1"

    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"

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


def _ros2_source_prefix() -> str:
    if not BASH_SOURCE.exists():
        return ""
    lines = []
    with open(BASH_SOURCE) as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return " && ".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch green_tracker_moveit with a clean environment (no Snap conflicts)."
    )
    parser.add_argument("--sensor-id",       default="default",
                        help="Sub-directory under resources/ for this sensor (default: default)")
    parser.add_argument("--target-z",        type=float, default=0.18,
                        help="Target Z height for pick in metres (default: 0.18)")
    parser.add_argument("--step-size",       type=float, default=0.05,
                        help="Cartesian step size in metres (default: 0.05)")
    parser.add_argument("--place-offset-x",  type=float, default=0.08,
                        help="Place X offset in metres (default: 0.08)")
    parser.add_argument("--place-offset-y",  type=float, default=0.08,
                        help="Place Y offset in metres (default: 0.08)")
    parser.add_argument("--color",           default="red",
                        help="Target colour for the tracker (default: red)")
    args = parser.parse_args()

    # -- Validate homography file --------------------------------------
    homography_path = RESOURCES_ROOT / args.sensor_id / "homography.txt"
    if not homography_path.exists():
        print(f"[error] homography.txt not found: {homography_path}", flush=True)
        sys.exit(1)

    # -- Build ros2 run command ----------------------------------------
    ros2_cmd = (
        f"ros2 run hello_moveit green_tracker_moveit "
        f"--ros-args "
        f"-p homography_path:={homography_path} "
        f"-p target_z:={args.target_z} "
        f"-p step_size:={args.step_size} "
        f"-p place_offset_x:={args.place_offset_x} "
        f"-p place_offset_y:={args.place_offset_y} "
        f"-p color:={args.color}"
    )

    # -- Diagnostics ---------------------------------------------------
    print(f"[tracker] launching  : green_tracker_moveit", flush=True)
    print(f"[tracker] homography : {homography_path}", flush=True)
    print(f"[tracker] target_z   : {args.target_z}", flush=True)
    print(f"[tracker] step_size  : {args.step_size}", flush=True)
    print(f"[tracker] color      : {args.color}", flush=True)

    # -- Launch --------------------------------------------------------
    # Start from the sanitized environment, then source the current workspace
    # setup from bash.source so ros2 resolves packages from the active install.
    env = _build_env()
    prefix = _ros2_source_prefix()
    cmd = ros2_cmd
    if prefix:
        cmd = f"{prefix} && exec {ros2_cmd}"
        print(f"[tracker] sourced    : {BASH_SOURCE}", flush=True)
    else:
        cmd = f"exec {ros2_cmd}"
        print(f"[tracker] warning    : missing {BASH_SOURCE}", flush=True)
    proc = subprocess.Popen(
        ["bash", "--login", "-c", cmd],
        stdout=sys.stdout,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    print(f"[tracker] PID {proc.pid}", flush=True)

    # -- Signal forwarding ---------------------------------------------
    def _forward(sig, _frame):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, OSError):
            pass

    signal.signal(signal.SIGINT,  _forward)
    signal.signal(signal.SIGTERM, _forward)

    rc = proc.wait()
    print(f"[tracker] exited rc={rc}", flush=True)
    sys.exit(rc)


if __name__ == "__main__":
    main()
