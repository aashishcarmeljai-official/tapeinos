"""
homography_collect.py — Homography Point Collection
====================================================
Usage:
    python3 homography_collect.py [--sensor-id <id>] [--color red|green|blue|yellow]

Saves homography_points.npz to ./resources/<sensor_id>/
Uses unified cylinder_detector.py for detection.
"""

import argparse
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
from tf2_ros import Buffer, TransformListener
import rclpy.duration
import rclpy.time

from cylinder_detector import detect_cylinder_top, draw_detection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_ROOT = PROJECT_ROOT / "resources"

CAMERA_TOPIC = "/video_frames"
MIN_POINTS   = 10


class HomographyCollectNode(Node):
    def __init__(self):
        super().__init__("homography_collect")
        self.tf_buffer   = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.bridge       = CvBridge()
        self.latest_frame = None
        self.frame_lock   = threading.Lock()
        self.create_subscription(Image, CAMERA_TOPIC, self._cb, 10)

    def _cb(self, msg):
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            frame = cv2.resize(frame, (640, 480))
            with self.frame_lock:
                self.latest_frame = frame
        except Exception:
            pass

    def get_frame(self):
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def get_robot_xy(self):
        t = self.tf_buffer.lookup_transform(
            "base_link", "tool0",
            rclpy.time.Time(),
            timeout=rclpy.duration.Duration(seconds=1.0))
        return t.transform.translation.x, t.transform.translation.y

    def wait_for_camera(self, timeout=10.0):
        start = time.time()
        while rclpy.ok():
            with self.frame_lock:
                if self.latest_frame is not None:
                    return True
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)
        return False

    def wait_for_tf(self):
        while rclpy.ok():
            try:
                self.tf_buffer.lookup_transform(
                    "base_link", "tool0",
                    rclpy.time.Time(),
                    timeout=rclpy.duration.Duration(seconds=1.0))
                return
            except Exception:
                time.sleep(0.1)


def draw_hud(frame, points, frozen, status, warning):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 80), (30, 30, 30), -1)
    color = (0, 255, 0) if len(points) >= MIN_POINTS else (0, 200, 255)
    cv2.putText(frame, f"Points: {len(points)}/{MIN_POINTS}",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    state_text  = "FROZEN — jog above, ENTER to record" if frozen else "LIVE — SPACE to freeze"
    state_color = (0, 200, 255) if frozen else (200, 200, 200)
    cv2.putText(frame, state_text,
                (w - 350, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, state_color, 1)
    cv2.putText(frame, "SPACE=freeze  ENTER=record  U=undo  ESC=save",
                (w - 350, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 180, 180), 1)
    cv2.rectangle(frame, (0, h - 65), (w, h), (30, 30, 30), -1)
    if warning:
        cv2.putText(frame, warning, (10, h - 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 100, 255), 1)
    if status:
        cv2.putText(frame, status, (10, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sensor-id", default="default")
    parser.add_argument("--color", default="red",
                        choices=["red", "green", "blue", "yellow"])
    args = parser.parse_args()

    resources_dir = RESOURCES_ROOT / args.sensor_id
    resources_dir.mkdir(parents=True, exist_ok=True)
    output_file   = resources_dir / "homography_points.npz"

    # Apply undistortion if calibration available
    K_new = D = K = None
    cal_path = resources_dir / "camera_params.npz"
    if cal_path.exists():
        cal      = np.load(str(cal_path))
        K        = cal["K"]
        D        = cal["D"]
        img_size = tuple(cal["img_size"].astype(int))
        K_new, _ = cv2.getOptimalNewCameraMatrix(K, D, img_size, 1, img_size)
        print("Undistortion: ON")
    else:
        print("Undistortion: OFF (no camera_params.npz)")

    rclpy.init()
    node     = HomographyCollectNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    ros_thread = threading.Thread(target=executor.spin, daemon=True)
    ros_thread.start()

    node.wait_for_tf()
    if not node.wait_for_camera():
        print("ERROR: No camera feed.")
        rclpy.shutdown()
        return

    pixel_points  = []
    robot_points  = []
    frozen        = False
    frozen_frame  = None
    frozen_result = None
    status_msg    = f"Move {args.color} cylinder into view. SPACE to freeze."
    warning_msg   = ""

    print(f"\nCollecting homography points — color={args.color}")
    print(f"Goal: {MIN_POINTS} points minimum")
    print(f"Output: {output_file}\n")

    while rclpy.ok():
        if not frozen:
            frame = node.get_frame()
            if frame is None:
                time.sleep(0.03)
                continue
            if K is not None:
                frame = cv2.undistort(frame, K, D, None, K_new)
        else:
            frame = frozen_frame.copy()

        display = frame.copy()

        live_result = None
        if not frozen:
            live_result = detect_cylinder_top(frame, args.color)
            if live_result:
                draw_detection(display, live_result)
        else:
            if frozen_result:
                # Draw frozen overlay in cyan
                cv2.circle(display, (frozen_result.cx, frozen_result.cy),
                           int(frozen_result.radius), (0, 200, 255), 2)
                cv2.circle(display, (frozen_result.cx, frozen_result.cy),
                           5, (0, 200, 255), -1)
                cv2.putText(display,
                            f"FROZEN ({frozen_result.cx},{frozen_result.cy})",
                            (frozen_result.cx + 20, frozen_result.cy - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

        for i, pp in enumerate(pixel_points):
            cv2.circle(display, (int(pp[0]), int(pp[1])), 5, (255, 100, 0), -1)
            cv2.putText(display, str(i + 1),
                        (int(pp[0]) + 8, int(pp[1])),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 100, 0), 1)

        display = draw_hud(display, pixel_points, frozen, status_msg, warning_msg)
        cv2.imshow("Homography Collector", display)
        key = cv2.waitKey(1)

        if key == 32:   # SPACE
            if not frozen:
                if live_result is None:
                    warning_msg = f"No {args.color} cylinder detected!"
                else:
                    frozen        = True
                    frozen_frame  = frame.copy()
                    frozen_result = live_result
                    warning_msg   = ""
                    status_msg    = (f"Frozen at ({live_result.cx},{live_result.cy}). "
                                     f"Jog robot above, then ENTER.")
            else:
                frozen        = False
                frozen_frame  = None
                frozen_result = None
                status_msg    = "Unfrozen."
                warning_msg   = ""

        elif key == 13:   # ENTER
            if not frozen:
                warning_msg = "Freeze first with SPACE!"
            else:
                try:
                    rx, ry = node.get_robot_xy()
                    pixel_points.append((frozen_result.cx, frozen_result.cy))
                    robot_points.append((rx, ry))
                    n          = len(pixel_points)
                    remaining  = max(0, MIN_POINTS - n)
                    warning_msg = ""
                    status_msg  = (f"Point #{n} recorded! "
                                   + (f"Need {remaining} more." if remaining > 0
                                      else "Enough! ESC to save."))
                    print(f"Point #{n} | pixel=({frozen_result.cx},{frozen_result.cy}) "
                          f"| robot=({rx:.4f},{ry:.4f})")
                    frozen        = False
                    frozen_frame  = None
                    frozen_result = None
                except Exception as e:
                    warning_msg = f"TF error: {e}"

        elif key in (ord('u'), ord('U')):
            if pixel_points:
                pixel_points.pop()
                robot_points.pop()
                status_msg  = f"Undone. {len(pixel_points)} remaining."
                warning_msg = ""

        elif key == 27:   # ESC
            break

    cv2.destroyAllWindows()

    if len(pixel_points) < 4:
        print("Need at least 4 points. Nothing saved.")
        rclpy.shutdown()
        return

    np.savez(str(output_file),
             pixel_points=np.array(pixel_points, dtype=np.float32),
             robot_points=np.array(robot_points, dtype=np.float32))
    print(f"\nSaved {len(pixel_points)} points → {output_file}")
    rclpy.shutdown()


if __name__ == "__main__":
    main()
