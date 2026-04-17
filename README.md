# Tapeinos Setup

Create a project-root [`bash.source`](/home/karunya/tapeinos/bash.source) file before running the app. This file is used by the dashboard and camera tools to source your ROS 2 workspaces.

Example:

```bash
source /opt/ros/humble/setup.bash
source $HOME/colcon_ws/install/local_setup.bash
source $HOME/microros_ws/install/local_setup.bash
source $HOME/ros2-starter-updated/install/setup.bash
```

The file must be located at the repo root:

```bash
/path/to/tapeinos/bash.source
```

If you use camera tracking, also create or refresh [`clean_env.json`]($HOME/tapeinos/sensors/camera/clean_env.json) from a working terminal session:

```bash
python3 -c "import os, json; print(json.dumps(dict(os.environ)))" > $HOME/tapeinos/sensors/camera/clean_env.json
```

Run the app from the project root:

```bash
python3 app.py
```

On first startup, the app will create:

```bash
$HOME/tapeinos/resources
```

## Docker

The repo now includes a `Dockerfile` that builds a ROS 2 Humble image with:

- MoveIt 2 and Fast DDS
- a `micro-ROS Agent` workspace
- `motoros2_client_interface_dependencies`
- the `ros2-starter-updated` workspace this dashboard launches
- the Tapeinos Flask app itself
- compatibility with a Raspberry Pi OS Trixie host by keeping ROS inside an Ubuntu 22.04 container

Build the image from the repo root:

```bash
docker build -t tapeinos:humble .
```

For Raspberry Pi OS Trixie on a 64-bit Raspberry Pi, use the helper script instead:

```bash
chmod +x docker/build-pi-trixie.sh docker/run-pi-trixie.sh
./docker/build-pi-trixie.sh
```

Run it with host networking, X11 forwarding, and whichever devices you need to pass through:

```bash
xhost +local:root

docker run --rm -it \
  --name tapeinos \
  --network host \
  --ipc host \
  -e DISPLAY=$DISPLAY \
  -e ROS_DOMAIN_ID=0 \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v $HOME/.Xauthority:/root/.Xauthority:ro \
  --device=/dev/video0 \
  --device=/dev/ttyUSB0 \
  tapeinos:humble
```

On Raspberry Pi OS Trixie, the simpler way is:

```bash
./docker/run-pi-trixie.sh
```

Notes:

- Remove `--device=/dev/video0` if you are not using a camera.
- Replace `--device=/dev/ttyUSB0` with the serial device for your ultrasonic sensor or robot adapter.
- `--network host` is the simplest way to let ROS 2 discovery and the micro-ROS UDP agent talk to the controller on your LAN.
- Inside the container, the entrypoint regenerates `bash.source` and `sensors/camera/clean_env.json` automatically.
- Raspberry Pi OS Trixie is Debian-based, but ROS 2 Humble binary support is for Ubuntu 22.04, so this setup intentionally runs an Ubuntu 22.04 ROS container on the Pi instead of switching the container base to Debian.
