FROM ros:humble-ros-base

ARG DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-lc"]

ENV ROS_DISTRO=humble
ENV ROS_PYTHON_VERSION=3
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ENV PYTHONUNBUFFERED=1
ENV TAPEINOS_ROOT=/opt/tapeinos
ENV TAPEINOS_PROJECT=/opt/tapeinos/tapeinos
ENV TAPEINOS_COLCON_WS=/opt/tapeinos/colcon_ws
ENV TAPEINOS_MICROROS_WS=/opt/tapeinos/microros_ws
ENV TAPEINOS_STARTER_WS=/opt/tapeinos/ros2-starter-updated

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    mesa-utils \
    python3-colcon-common-extensions \
    python3-flask \
    python3-numpy \
    python3-opencv \
    python3-rosdep \
    python3-serial \
    python3-vcstool \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# ── ROS packages ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-cv-bridge \
    ros-humble-joint-state-publisher \
    ros-humble-joint-state-publisher-gui \
    ros-humble-rmw-fastrtps-cpp \
    ros-humble-ros2-controllers \
    ros-humble-ros2-control \
    ros-humble-tf2-ros \
    ros-humble-tf2-tools \
    ros-humble-vision-opencv \
    ros-humble-xacro \
    && rm -rf /var/lib/apt/lists/*

# ── MoveIt ────────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-moveit \
    ros-humble-moveit-ros-planning-interface \
    ros-humble-moveit-servo \
    ros-humble-moveit-setup-assistant \
    && rm -rf /var/lib/apt/lists/*

# ── micro-ROS build dependencies + rosdep bootstrap ──────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    bison \
    clang \
    clang-tidy \
    cmake \
    flex \
    g++ \
    gcc \
    libasio-dev \
    libncurses5-dev \
    libtinyxml2-dev \
    make \
    python3-dev \
    python3-pip \
    usbutils \
    wget \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /etc/ros/rosdep/sources.list.d \
    && rosdep init 2>/dev/null || true \
    && rosdep update \
    && mkdir -p "${TAPEINOS_ROOT}"

# ── micro-ROS agent ───────────────────────────────────────────────────────────
RUN mkdir -p "${TAPEINOS_MICROROS_WS}/src" \
    && git clone --branch humble --depth 1 \
        https://github.com/micro-ROS/micro_ros_setup.git \
        "${TAPEINOS_MICROROS_WS}/src/micro_ros_setup" \
    && source /opt/ros/${ROS_DISTRO}/setup.bash \
    && cd "${TAPEINOS_MICROROS_WS}" \
    && rosdep install --from-paths src --ignore-src -y \
    && colcon build \
    && source install/local_setup.bash \
    && ros2 run micro_ros_setup create_agent_ws.sh \
    && ros2 run micro_ros_setup build_agent.sh

# ── MotoROS2 client interface dependencies ────────────────────────────────────
RUN mkdir -p "${TAPEINOS_COLCON_WS}/src" \
    && git clone --branch master --depth 1 \
        https://github.com/Yaskawa-Global/motoros2_client_interface_dependencies.git \
        "${TAPEINOS_COLCON_WS}/src/motoros2_client_interface_dependencies" \
    && vcs import \
        --input "${TAPEINOS_COLCON_WS}/src/motoros2_client_interface_dependencies/source_deps.repos" \
        "${TAPEINOS_COLCON_WS}/src" \
    && source /opt/ros/${ROS_DISTRO}/setup.bash \
    && rosdep install --from-paths "${TAPEINOS_COLCON_WS}/src" --ignore-src -r -y \
    && cd "${TAPEINOS_COLCON_WS}" \
    && colcon build \
        --packages-up-to motoros2_client_interface_dependencies \
        --cmake-args -DCMAKE_BUILD_TYPE=Release

# ── ROS 2 starter workspace ───────────────────────────────────────────────────
RUN set -ex && \
    pip3 install "opencv-python<4.9" "numpy<2" && \
    git clone --depth 1 \
        https://github.com/aashishcarmeljai-official/ros2-starter-updated.git \
        "${TAPEINOS_STARTER_WS}" && \
    \
    # 🔥 remove problematic dependency completely
    sed -i '/warehouse_ros_mongo/d' \
        $(find ${TAPEINOS_STARTER_WS} -name package.xml) && \
    \
    source /opt/ros/${ROS_DISTRO}/setup.bash && \
    source "${TAPEINOS_MICROROS_WS}/install/local_setup.bash" && \
    source "${TAPEINOS_COLCON_WS}/install/local_setup.bash" && \
    \
    apt-get update && apt-get install -y \
        ros-humble-image-transport \
        ros-humble-vision-msgs && \
    \
    rosdep update && \
    rosdep install --from-paths "${TAPEINOS_STARTER_WS}" \
        --ignore-src -r -y && \
    \
    cd "${TAPEINOS_STARTER_WS}" && \
    colcon build --event-handlers console_direct+

# ── Application ───────────────────────────────────────────────────────────────
WORKDIR ${TAPEINOS_PROJECT}

COPY . ${TAPEINOS_PROJECT}

RUN chmod +x "${TAPEINOS_PROJECT}/docker/entrypoint.sh" \
    && mkdir -p "${TAPEINOS_PROJECT}/resources"

EXPOSE 5000

ENTRYPOINT ["/opt/tapeinos/tapeinos/docker/entrypoint.sh"]
CMD ["python3", "app.py"]