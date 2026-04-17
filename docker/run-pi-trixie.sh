#!/usr/bin/env bash
# docker/run-pi-trixie.sh
# Run the tapeinos container on a Raspberry Pi 5 running Raspberry Pi OS Trixie.
#
# Usage:
#   ./docker/run-pi-trixie.sh [image_name]
#
# Environment variables (all optional, sensible defaults shown):
#   CAMERA_DEVICE   – V4L2 device node  (default: /dev/video0)
#   SERIAL_DEVICE   – Serial port        (default: /dev/ttyUSB0)
#   ROS_DOMAIN_ID   – ROS 2 domain ID   (default: 0)
set -euo pipefail

IMAGE_NAME="${1:-tapeinos:pi-trixie}"

CAMERA_DEVICE="${CAMERA_DEVICE:-/dev/video0}"
SERIAL_DEVICE="${SERIAL_DEVICE:-/dev/ttyUSB0}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

# ── X11 display forwarding ────────────────────────────────────────────────────
# On Trixie / Wayland the DISPLAY variable may be unset.  Fall back to :0 so
# RViz / rqt still launch when an X server is running.
DISPLAY_VALUE="${DISPLAY:-:0}"
XAUTHORITY_VALUE="${XAUTHORITY:-${HOME}/.Xauthority}"

# Allow the container's root user to connect to the host X server.
if command -v xhost &>/dev/null; then
  xhost +local:root 2>/dev/null || true
fi

# ── Optional device passthrough ───────────────────────────────────────────────
DEVICE_ARGS=()

if [[ -e "${CAMERA_DEVICE}" ]]; then
  DEVICE_ARGS+=(--device="${CAMERA_DEVICE}")
  # Also pass the media controller node required by libcamera / Pi Camera on Pi 5.
  [[ -e /dev/media0 ]] && DEVICE_ARGS+=(--device=/dev/media0)
  [[ -e /dev/media1 ]] && DEVICE_ARGS+=(--device=/dev/media1)
fi

if [[ -e "${SERIAL_DEVICE}" ]]; then
  DEVICE_ARGS+=(--device="${SERIAL_DEVICE}")
fi

# ── GPIO / hardware access (Pi 5) ────────────────────────────────────────────
# Uncomment if your application accesses GPIO directly via /dev/gpiochip*.
# DEVICE_ARGS+=(--device=/dev/gpiochip0 --device=/dev/gpiochip4)

# ── Launch ────────────────────────────────────────────────────────────────────
exec docker run --rm -it \
  --name tapeinos \
  --platform linux/arm64 \
  --network host \
  --ipc host \
  -e DISPLAY="${DISPLAY_VALUE}" \
  -e XAUTHORITY=/root/.Xauthority \
  -e ROS_DOMAIN_ID="${ROS_DOMAIN_ID}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v "${XAUTHORITY_VALUE}:/root/.Xauthority:ro" \
  "${DEVICE_ARGS[@]}" \
  "${IMAGE_NAME}"