#!/usr/bin/env bash
# docker/entrypoint.sh
set -eo pipefail

PROJECT_ROOT="${TAPEINOS_PROJECT:-/opt/tapeinos/tapeinos}"
BASH_SOURCE_FILE="${PROJECT_ROOT}/bash.source"
CLEAN_ENV_FILE="${PROJECT_ROOT}/sensors/camera/clean_env.json"

mkdir -p "${PROJECT_ROOT}/resources"

# Write a convenience file so interactive shells inside the container can
# source all overlay workspaces in one shot.
cat > "${BASH_SOURCE_FILE}" <<'EOF'
source /opt/ros/humble/setup.bash
test -f /opt/tapeinos/colcon_ws/install/local_setup.bash   && source /opt/tapeinos/colcon_ws/install/local_setup.bash   || true
test -f /opt/tapeinos/microros_ws/install/local_setup.bash && source /opt/tapeinos/microros_ws/install/local_setup.bash || true
test -f /opt/tapeinos/ros2-starter-updated/install/setup.bash && source /opt/tapeinos/ros2-starter-updated/install/setup.bash || true
EOF

# Capture the clean environment once (used by the camera sensor module to
# distinguish inherited vs. ROS-injected variables).
if [[ ! -f "${CLEAN_ENV_FILE}" ]]; then
  python3 -c 'import json, os; print(json.dumps(dict(os.environ)))' > "${CLEAN_ENV_FILE}"
fi

# Source all ROS overlays before handing off to CMD.
source /opt/ros/humble/setup.bash
test -f /opt/tapeinos/colcon_ws/install/local_setup.bash   && source /opt/tapeinos/colcon_ws/install/local_setup.bash   || true
test -f /opt/tapeinos/microros_ws/install/local_setup.bash && source /opt/tapeinos/microros_ws/install/local_setup.bash || true
test -f /opt/tapeinos/ros2-starter-updated/install/setup.bash && source /opt/tapeinos/ros2-starter-updated/install/setup.bash || true

cd "${PROJECT_ROOT}"
exec "$@"