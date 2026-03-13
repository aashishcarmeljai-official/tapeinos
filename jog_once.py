#!/usr/bin/env python3
"""
jog_once.py — MoveIt2 jog module for Tapeinos
===============================================
Two usage modes:

  1. IMPORTED by app.py (fast path):
       from jog_once import JogRunner
       runner = JogRunner()          # starts rclpy + spin thread once
       runner.execute('w')           # non-blocking, returns quickly
       runner.shutdown()             # call on app exit

  2. STANDALONE CLI (unchanged behaviour):
       python3 jog_once.py <key>

Cartesian : w s a d r f
Joint     : 1 2 3 4 5 6  (+step)    ! @ # $ % ^  (-step)
Mode      : c (cartesian)  j (joint)
Gripper   : o (open)  p (close)

Mode is persisted in /tmp/jog_state.json between calls.
"""

from __future__ import annotations

import sys
import json
import time
import threading
import logging
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor

from sensor_msgs.msg import JointState
from moveit_msgs.action import MoveGroup, ExecuteTrajectory      # type: ignore
from moveit_msgs.msg import (                                    # type: ignore
    Constraints, JointConstraint, MoveItErrorCodes, RobotState,
)
from moveit_msgs.srv import GetCartesianPath                     # type: ignore
from geometry_msgs.msg import Pose
import tf2_ros

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_FILE    = "/tmp/jog_state.json"
STEP_CART     = 0.02
STEP_JOINT    = 0.1
JOINT_NAMES   = ["joint_1", "joint_2", "joint_3",
                 "joint_4", "joint_5", "joint_6"]
BASE_FRAME    = "base_link"
EEF_CANDIDATES = ["flange", "tool0", "link_6", "link_6_t", "link_t"]

JOINT_MAP = {
    '1': (0,  STEP_JOINT), '!': (0, -STEP_JOINT),
    '2': (1,  STEP_JOINT), '@': (1, -STEP_JOINT),
    '3': (2,  STEP_JOINT), '#': (2, -STEP_JOINT),
    '4': (3,  STEP_JOINT), '$': (3, -STEP_JOINT),
    '5': (4,  STEP_JOINT), '%': (4, -STEP_JOINT),
    '6': (5,  STEP_JOINT), '^': (5, -STEP_JOINT),
}


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"mode": "cartesian"}


def save_state(s: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)


# ---------------------------------------------------------------------------
# JogNode  (unchanged logic, spin_for / spin_until use the executor now)
# ---------------------------------------------------------------------------

class JogNode(Node):
    def __init__(self) -> None:
        super().__init__("jog_once")
        self._js:   Optional[JointState] = None
        self._lock  = threading.Lock()

        # Stop signals from ultrasonic sensors: topic -> bool
        self._stop_signals: dict[str, bool] = {}
        self._stop_lock = threading.Lock()
        # Back-reference to executor — set by JogRunner after creation
        self._executor = None

        self.create_subscription(JointState, "/joint_states", self._js_cb, 10)
        self._move_client = ActionClient(self, MoveGroup,           "/move_action")
        self._exec_client = ActionClient(self, ExecuteTrajectory,   "/execute_trajectory")
        self._cart_client = self.create_client(GetCartesianPath,    "/compute_cartesian_path")
        self._tf_buffer   = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)

    def _js_cb(self, msg: JointState) -> None:
        with self._lock:
            self._js = msg

    # Stop-signal API
    def subscribe_stop_topic(self, topic: str) -> None:
        from std_msgs.msg import Bool
        with self._stop_lock:
            if topic in self._stop_signals:
                return
            self._stop_signals[topic] = False
        def _cb(msg, t=topic):
            with self._stop_lock:
                self._stop_signals[t] = msg.data
        self.create_subscription(Bool, topic, _cb, 10)
        self.get_logger().info(f"[stop-guard] subscribed to {topic}")

    def unsubscribe_stop_topic(self, topic: str) -> None:
        with self._stop_lock:
            self._stop_signals.pop(topic, None)

    def is_stopped(self) -> tuple:
        with self._stop_lock:
            for topic, active in self._stop_signals.items():
                if active:
                    return True, topic
        return False, ""

    # ------------------------------------------------------------------
    # Spinning helpers — work whether driven by spin_once or an Executor
    # ------------------------------------------------------------------

    def spin_for(self, secs: float) -> None:
        # Executor drives callbacks independently; just sleep here.
        time.sleep(secs)

    def spin_until(self, future, timeout: float = 10.0,
                   executor=None, abort_if_stopped: bool = False) -> bool:
        """
        Wait for a future without re-entering the executor.
        Uses a threading.Event signalled by the future's done callback so
        that the executor thread (which drives this node) keeps spinning
        freely while the caller thread just waits.
        """
        done_event = threading.Event()
        future.add_done_callback(lambda _: done_event.set())

        # If future already done before we attach the callback
        if future.done():
            done_event.set()

        deadline = time.time() + timeout
        poll_interval = 0.05  # check stop signal every 50 ms

        while True:
            # Stop signal always takes priority
            if abort_if_stopped:
                stopped, topic = self.is_stopped()
                if stopped:
                    self.get_logger().warn(
                        f"[stop-guard] motion aborted — stop signal on {topic}")
                    return False
            if done_event.is_set():
                return future.done()
            remaining = deadline - time.time()
            if remaining <= 0:
                return False  # timed out
            done_event.wait(timeout=min(poll_interval, remaining))

    def wait_for_joint_state(self, timeout: float = 10.0) -> Optional[JointState]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
            with self._lock:
                if self._js is not None:
                    return self._js
        return None

    def get_joint_values(self) -> Optional[list]:
        with self._lock:
            js = self._js
        if js is None:
            return None
        m = dict(zip(js.name, js.position))
        return [m.get(n, 0.0) for n in JOINT_NAMES]

    @staticmethod
    def make_robot_state(joint_values: list) -> RobotState:
        rs = RobotState()
        rs.joint_state.name     = list(JOINT_NAMES)
        rs.joint_state.position = list(joint_values)
        return rs

    def get_eef_pose(self):
        self.spin_for(0.3)
        for link in EEF_CANDIDATES:
            try:
                t = self._tf_buffer.lookup_transform(
                    BASE_FRAME, link,
                    rclpy.time.Time(),
                    timeout=rclpy.duration.Duration(seconds=2.0),
                )
                self.get_logger().info(f"EEF link: {link}")
                pose = Pose()
                pose.position.x    = t.transform.translation.x
                pose.position.y    = t.transform.translation.y
                pose.position.z    = t.transform.translation.z
                pose.orientation.x = t.transform.rotation.x
                pose.orientation.y = t.transform.rotation.y
                pose.orientation.z = t.transform.rotation.z
                pose.orientation.w = t.transform.rotation.w
                return pose, link
            except tf2_ros.TransformException:
                continue
        return None, None

    def _cancel_and_drain(self, gh, label: str = "") -> None:
        """
        Cancel a goal handle and wait (event-based, no executor re-entry)
        until cancellation is acknowledged or 2 s timeout elapses.
        """
        try:
            cancel_fut = gh.cancel_goal_async()
            done_event = threading.Event()
            cancel_fut.add_done_callback(lambda _: done_event.set())
            if cancel_fut.done():
                done_event.set()
            done_event.wait(timeout=2.0)
            # Small fixed sleep — lets MoveIt finish internal cleanup
            time.sleep(0.15)
            if label:
                self.get_logger().info(f"[cancel] {label} cancelled and drained")
        except Exception as exc:
            self.get_logger().warn(f"[cancel] drain error: {exc}")

    def move_joints(self, joint_values: list, timeout: float = 30.0) -> bool:
        if not self._move_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("MoveGroup action server not available")
            return False

        goal = MoveGroup.Goal()
        req  = goal.request
        req.group_name                      = "manipulator"
        req.num_planning_attempts           = 5
        req.allowed_planning_time           = 10.0
        req.max_velocity_scaling_factor     = 0.2
        req.max_acceleration_scaling_factor = 0.2

        c = Constraints()
        for name, val in zip(JOINT_NAMES, joint_values):
            jc = JointConstraint()
            jc.joint_name      = name
            jc.position        = float(val)
            jc.tolerance_above = 0.01
            jc.tolerance_below = 0.01
            jc.weight          = 1.0
            c.joint_constraints.append(jc)
        req.goal_constraints.append(c)

        fut = self._move_client.send_goal_async(goal)
        if not self.spin_until(fut, timeout=5.0, executor=self._executor):
            self.get_logger().error("Goal send timed out")
            return False
        gh = fut.result()
        if not gh.accepted:
            self.get_logger().error("Goal rejected")
            return False

        res_fut = gh.get_result_async()
        if not self.spin_until(res_fut, timeout=timeout,
                               executor=self._executor, abort_if_stopped=True):
            # Could be timeout or stop signal — cancel and drain before returning
            self.get_logger().warn("Move interrupted — cancelling goal")
            self._cancel_and_drain(gh, "move_joints")
            return False

        ok = res_fut.result().result.error_code.val == MoveItErrorCodes.SUCCESS
        if not ok:
            self.get_logger().warn(
                f"Move failed, code={res_fut.result().result.error_code.val}")
        return ok

    def move_cartesian(self, joint_values: list, key: str, timeout: float = 30.0) -> bool:
        pose, eef_link = self.get_eef_pose()
        if pose is None:
            self.get_logger().error(
                f"TF lookup failed for all candidates: {EEF_CANDIDATES}")
            return False

        if   key == 'w': pose.position.x += STEP_CART
        elif key == 's': pose.position.x -= STEP_CART
        elif key == 'a': pose.position.y += STEP_CART
        elif key == 'd': pose.position.y -= STEP_CART
        elif key == 'r': pose.position.z += STEP_CART
        elif key == 'f': pose.position.z -= STEP_CART

        if not self._cart_client.wait_for_service(timeout_sec=5.0):
            self.get_logger().error("/compute_cartesian_path not available")
            return False

        req = GetCartesianPath.Request()
        req.header.frame_id  = BASE_FRAME
        req.start_state      = self.make_robot_state(joint_values)
        req.group_name       = "manipulator"
        req.link_name        = eef_link
        req.waypoints        = [pose]
        req.max_step         = 0.01
        req.jump_threshold   = 0.0
        req.avoid_collisions = True

        fut = self._cart_client.call_async(req)
        if not self.spin_until(fut, timeout=10.0, executor=self._executor):
            self.get_logger().error("/compute_cartesian_path timed out")
            return False

        res = fut.result()
        if res.fraction < 0.95:
            self.get_logger().warn(
                f"Cartesian path fraction too low: {res.fraction:.2f}")
            return False

        self.get_logger().info(
            f"Path computed (fraction={res.fraction:.2f}), executing…")

        if not self._exec_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("/execute_trajectory not available")
            return False

        exec_goal            = ExecuteTrajectory.Goal()
        exec_goal.trajectory = res.solution

        fut2 = self._exec_client.send_goal_async(exec_goal)
        if not self.spin_until(fut2, timeout=5.0, executor=self._executor):
            self.get_logger().error("execute_trajectory send timed out")
            return False
        gh2 = fut2.result()
        if not gh2.accepted:
            self.get_logger().error("execute_trajectory rejected")
            return False

        res_fut2 = gh2.get_result_async()
        if not self.spin_until(res_fut2, timeout=timeout,
                               executor=self._executor, abort_if_stopped=True):
            self.get_logger().warn("execute_trajectory interrupted — cancelling goal")
            self._cancel_and_drain(gh2, "execute_trajectory")
            return False

        ok = res_fut2.result().result.error_code.val == MoveItErrorCodes.SUCCESS
        if not ok:
            self.get_logger().warn(
                f"execute_trajectory failed, "
                f"code={res_fut2.result().result.error_code.val}")
        return ok

    def do_gripper(self, open_gripper: bool) -> bool:
        from motoros2_interfaces.srv import WriteSingleIO  # type: ignore
        client = self.create_client(WriteSingleIO, "/yaskawa/write_single_io")
        if not client.wait_for_service(timeout_sec=3.0):
            self.get_logger().error("gripper service not available")
            return False

        def write_io(addr: int, val: int) -> bool:
            req = WriteSingleIO.Request()
            req.address = addr
            req.value   = val
            fut = client.call_async(req)
            self.spin_until(fut, timeout=3.0)
            return fut.done() and fut.result().success

        if open_gripper:
            self.get_logger().info("gripper OPEN")
            write_io(10012, 1)
            write_io(10011, 0)
        else:
            self.get_logger().info("gripper CLOSE")
            write_io(10011, 1)
            write_io(10012, 0)
        return True


# ---------------------------------------------------------------------------
# JogRunner  — persistent, importable, used by app.py
# ---------------------------------------------------------------------------

class JogRunner:
    """
    Owns a single rclpy context + JogNode + background spin thread.
    Call execute(key) from any thread; it blocks until the motion completes
    (or errors) but does NOT restart rclpy each time.

    Lifecycle::
        runner = JogRunner()   # call once at app startup
        runner.execute('w')    # call per jog command
        runner.shutdown()      # call at app exit
    """

    def __init__(self) -> None:
        self._lock     = threading.Lock()   # serialise concurrent jog calls
        self._ready    = threading.Event()
        self._node:    Optional[JogNode] = None
        self._executor = None
        self._thread:  Optional[threading.Thread] = None
        self._active   = False
        self._pending_stop_topics: set = set()
        self._pending_lock = threading.Lock()

        self._thread = threading.Thread(
            target=self._spin_loop,
            daemon=True,
            name="jog-runner-spin",
        )
        self._thread.start()
        # Wait up to 15 s for rclpy + joint state
        if not self._ready.wait(timeout=15.0):
            log.warning("[JogRunner] timed out waiting for joint state — "
                        "commands will still work once ROS2 is up")

    # ------------------------------------------------------------------
    # Background spin loop
    # ------------------------------------------------------------------

    def _spin_loop(self) -> None:
        owns_rclpy = False
        try:
            # Initialise rclpy only if no context is active yet.
            # UltrasonicRunner may have already called rclpy.init().
            if not rclpy.ok():
                rclpy.init()
                owns_rclpy = True

            self._node     = JogNode()
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)
            # Give JogNode a back-reference so spin_until can use the executor
            self._node._executor = self._executor
            self._active   = True
            self._flush_pending_topics()

            # Signal ready once we have a joint state (or after 10 s)
            deadline = time.time() + 10.0
            while time.time() < deadline and self._active:
                self._executor.spin_once(timeout_sec=0.05)
                if self._node._js is not None:
                    break
            self._ready.set()

            # spin() blocks here and processes all callbacks until shutdown
            self._executor.spin()

        except Exception as exc:
            log.error(f"[JogRunner] spin loop error: {exc}")
            self._ready.set()
        finally:
            try:
                # Drain any pending callbacks before destroying
                if self._executor and self._node:
                    drain_end = time.time() + 0.5
                    while time.time() < drain_end:
                        self._executor.spin_once(timeout_sec=0.05)
                if self._node:
                    self._node.destroy_node()
                # Only shut down rclpy if we were the ones who initialised it.
                if owns_rclpy:
                    rclpy.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, key: str) -> tuple[bool, str]:
        """
        Execute one jog command synchronously (blocks caller's thread).
        Returns (success: bool, message: str).
        """
        if not self._active or self._node is None:
            return False, "JogRunner not initialised"

        key = key[0]

        # Mode switches — pure state, no motion
        if key == 'c':
            state = load_state(); state["mode"] = "cartesian"; save_state(state)
            return True, "mode → cartesian"
        if key == 'j':
            state = load_state(); state["mode"] = "joint";     save_state(state)
            return True, "mode → joint"

        with self._lock:   # one motion at a time
            blocked, blocker = self._node.is_stopped()
            if blocked:
                return False, f"BLOCKED by stop signal on {blocker}"

            joints = self._node.get_joint_values()
            if joints is None:
                return False, "no joint state received"

            # Gripper
            if key in ('o', 'p'):
                ok = self._node.do_gripper(open_gripper=(key == 'o'))
                return ok, "gripper open" if key == 'o' else "gripper close"

            # Joint jog
            if key in JOINT_MAP:
                idx, delta = JOINT_MAP[key]
                joints[idx] += delta
                msg = f"joint J{idx+1} {'+' if delta > 0 else ''}{delta:.2f} rad"
                log.info(f"[JogRunner] {msg}")
                ok = self._node.move_joints(joints)
                return ok, msg

            # Cartesian jog
            if key in "wsadrf":
                log.info(f"[JogRunner] cartesian '{key}'")
                ok = self._node.move_cartesian(joints, key)
                return ok, f"cartesian '{key}'"

            return False, f"unknown key '{key}'"

    def register_stop_topic(self, topic: str) -> None:
        if self._node is not None:
            self._node.subscribe_stop_topic(topic)
            log.info(f"[JogRunner] stop-guard registered: {topic}")
        else:
            with self._pending_lock:
                self._pending_stop_topics.add(topic)
            log.info(f"[JogRunner] stop-guard queued (node not ready): {topic}")

    def _flush_pending_topics(self) -> None:
        with self._pending_lock:
            topics = list(self._pending_stop_topics)
            self._pending_stop_topics.clear()
        for topic in topics:
            self._node.subscribe_stop_topic(topic)
            log.info(f"[JogRunner] stop-guard flushed: {topic}")

    def unregister_stop_topic(self, topic: str) -> None:
        with self._pending_lock:
            self._pending_stop_topics.discard(topic)
        if self._node is not None:
            self._node.unsubscribe_stop_topic(topic)

    def shutdown(self) -> None:
        """Stop the spin loop and clean up rclpy."""
        self._active = False
        if self._executor:
            self._executor.shutdown()  # breaks out of executor.spin()
        if self._thread:
            self._thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Module-level singleton — created lazily when app.py first imports
# ---------------------------------------------------------------------------

_runner: Optional[JogRunner] = None
_runner_lock = threading.Lock()


def get_runner() -> JogRunner:
    """Return (or lazily create) the module-level JogRunner singleton."""
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = JogRunner()
    return _runner


def shutdown_runner() -> None:
    global _runner
    if _runner is not None:
        _runner.shutdown()
        _runner = None


# ---------------------------------------------------------------------------
# Standalone CLI  (python3 jog_once.py <key>)
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: jog_once.py <key>", file=sys.stderr)
        sys.exit(1)

    key   = sys.argv[1][0]
    state = load_state()

    if key == 'c':
        state["mode"] = "cartesian"; save_state(state)
        print("mode → cartesian"); return
    if key == 'j':
        state["mode"] = "joint"; save_state(state)
        print("mode → joint"); return

    rclpy.init()
    node = JogNode()

    print("waiting for joint state…")
    if node.wait_for_joint_state(timeout=10.0) is None:
        print("ERROR: no joint state received", file=sys.stderr)
        node.destroy_node(); rclpy.shutdown(); sys.exit(1)

    if key in ('o', 'p'):
        ok = node.do_gripper(open_gripper=(key == 'o'))
        node.destroy_node(); rclpy.shutdown(); sys.exit(0 if ok else 1)

    joints = node.get_joint_values()

    if key in JOINT_MAP:
        idx, delta = JOINT_MAP[key]
        joints[idx] += delta
        print(f"joint J{idx+1} {'+' if delta > 0 else ''}{delta:.2f} rad")
        ok = node.move_joints(joints)
    elif key in "wsadrf":
        print(f"cartesian '{key}'")
        ok = node.move_cartesian(joints, key)
    else:
        print(f"unknown key '{key}'", file=sys.stderr)
        node.destroy_node(); rclpy.shutdown(); sys.exit(1)

    node.destroy_node()
    rclpy.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()