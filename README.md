# Tapeinos Setup

Create a root-level [`bash.source`](/home/karunya/tapeinos/bash.source) file before running the app. This file is used by the dashboard and camera tools to source your ROS 2 workspaces.

Example:

```bash
source /opt/ros/humble/setup.bash
source $HOME/colcon_ws/install/local_setup.bash
source $HOME/microros_ws/install/local_setup.bash
source $HOME/ros2-starter-updated/install/setup.bash
```

The file must be located at:

```bash
$HOME/tapeinos/bash.source
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
