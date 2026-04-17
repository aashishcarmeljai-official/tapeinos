#!/usr/bin/env bash
# docker/build-pi-trixie.sh
# Build the tapeinos image for Raspberry Pi 5 (ARM64) on Raspberry Pi OS Trixie.
#
# Usage:
#   ./docker/build-pi-trixie.sh [image_name]
#
# Default image name: tapeinos:pi-trixie
set -euo pipefail

IMAGE_NAME="${1:-tapeinos:pi-trixie}"

# Ensure buildx is available and the arm64 emulator is registered.
# On Trixie this is handled by qemu-user-static; the line below is a no-op
# if already registered.
if ! docker buildx inspect tapeinos-builder &>/dev/null; then
  docker buildx create --name tapeinos-builder --use
fi

docker buildx build \
  --platform linux/arm64 \
  --load \
  -t "${IMAGE_NAME}" \
  .

echo ""
echo "    Image built: ${IMAGE_NAME}"
echo "    Run it with: ./docker/run-pi-trixie.sh ${IMAGE_NAME}"