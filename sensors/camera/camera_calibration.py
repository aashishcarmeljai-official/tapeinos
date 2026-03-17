"""
camera_calibration.py — ChArUco Camera Calibration
====================================================
Usage:
    python3 camera_calibration.py [--sensor-id <id>]

Saves camera_params.npz to ./resources/<sensor_id>/
Requires cam_pub to be running (publishes /video_frames).
"""

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import cv2
from cv2 import aruco
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_ROOT = PROJECT_ROOT / "resources"

# ── Board parameters ──────────────────────────────────────────────────────────
SQUARES_X      = 18
SQUARES_Y      = 11
SQUARE_LENGTH  = 0.019
MARKER_LENGTH  = 0.014
ARUCO_DICT     = aruco.getPredefinedDictionary(aruco.DICT_5X5_100)
MIN_CAPTURES   = 15
CAMERA_TOPIC   = "/video_frames"

board = aruco.CharucoBoard(
    (SQUARES_X, SQUARES_Y), SQUARE_LENGTH, MARKER_LENGTH, ARUCO_DICT)
charuco_detector = aruco.CharucoDetector(
    board, aruco.CharucoParameters(), aruco.DetectorParameters())


# ── ROS node ──────────────────────────────────────────────────────────────────

class CameraNode(Node):
    def __init__(self):
        super().__init__("charuco_calibration")
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
        except Exception as e:
            self.get_logger().warn(f"Image error: {e}")

    def get_frame(self):
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def wait_for_camera(self, timeout=15.0):
        start = time.time()
        while rclpy.ok():
            with self.frame_lock:
                if self.latest_frame is not None:
                    return True
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)
        return False


def draw_hud(frame, n_captures, n_corners, status, warning):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 70), (30, 30, 30), -1)
    color = (0, 255, 0) if n_captures >= MIN_CAPTURES else (0, 200, 255)
    cv2.putText(frame, f"Captures: {n_captures}/{MIN_CAPTURES}",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    cv2.putText(frame, f"Corners: {n_corners}",
                (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
    cv2.putText(frame, "SPACE=capture  ESC=calibrate  U=undo",
                (w - 340, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
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
    parser.add_argument("--sensor-id", default="default",
                        help="Sensor ID for resource directory")
    args = parser.parse_args()

    resources_dir = RESOURCES_ROOT / args.sensor_id
    resources_dir.mkdir(parents=True, exist_ok=True)
    output_file   = resources_dir / "camera_params.npz"
    snapshot_dir  = resources_dir / "calibration_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    rclpy.init()
    node     = CameraNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    ros_thread = threading.Thread(target=executor.spin, daemon=True)
    ros_thread.start()

    if not node.wait_for_camera():
        print("ERROR: No camera feed.")
        rclpy.shutdown()
        return

    all_charuco_corners = []
    all_charuco_ids     = []
    image_size          = None
    status_msg          = "Hold board in view. SPACE to capture."
    warning_msg         = ""
    n_captures          = 0

    print(f"\nSaving to: {output_file}")
    print(f"Goal: {MIN_CAPTURES} captures minimum\n")

    while rclpy.ok():
        frame = node.get_frame()
        if frame is None:
            time.sleep(0.03)
            continue

        display    = frame.copy()
        gray       = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        image_size = (gray.shape[1], gray.shape[0])

        charuco_corners, charuco_ids, marker_corners, marker_ids = \
            charuco_detector.detectBoard(gray)
        n_corners = len(charuco_corners) if charuco_corners is not None else 0

        if marker_corners:
            aruco.drawDetectedMarkers(display, marker_corners, marker_ids)
        if charuco_corners is not None and charuco_ids is not None and n_corners >= 6:
            aruco.drawDetectedCornersCharuco(
                display, charuco_corners, charuco_ids, (0, 255, 0))

        display = draw_hud(display, n_captures, n_corners,
                           status_msg, warning_msg)
        cv2.imshow("ChArUco Calibration", display)
        key = cv2.waitKey(1)

        if key == 32:   # SPACE
            if charuco_corners is None or charuco_ids is None:
                warning_msg = "No board detected!"
            elif n_corners < 6:
                warning_msg = f"Only {n_corners} corners. Need 6."
            else:
                all_charuco_corners.append(charuco_corners)
                all_charuco_ids.append(charuco_ids)
                n_captures += 1
                cv2.imwrite(str(snapshot_dir / f"snap_{n_captures:03d}.png"), frame)
                remaining   = max(0, MIN_CAPTURES - n_captures)
                warning_msg = ""
                status_msg  = (f"Captured #{n_captures}. "
                               + (f"Need {remaining} more." if remaining > 0
                                  else "Enough! ESC to calibrate."))

        elif key in (ord('u'), ord('U')):
            if all_charuco_corners:
                all_charuco_corners.pop()
                all_charuco_ids.pop()
                n_captures -= 1
                status_msg  = f"Undone. {n_captures} remaining."
                warning_msg = ""

        elif key == 27:   # ESC
            if n_captures < 4:
                warning_msg = f"Need at least 4! Have {n_captures}."
            else:
                break

    cv2.destroyAllWindows()

    if n_captures < 4:
        print("Not enough captures.")
        rclpy.shutdown()
        return

    print(f"\nCalibrating with {n_captures} captures…")
    ret, K, D, rvecs, tvecs = aruco.calibrateCameraCharuco(
        charucoCorners = all_charuco_corners,
        charucoIds     = all_charuco_ids,
        board          = board,
        imageSize      = image_size,
        cameraMatrix   = None,
        distCoeffs     = None,
    )

    print(f"RMS: {ret:.4f} px")
    print(f"K:\n{K}")
    print(f"D:\n{D}")

    np.savez(str(output_file),
             K        = K,
             D        = D,
             rvecs    = np.array(rvecs),
             tvecs    = np.array(tvecs),
             img_size = np.array(image_size),
             rms      = np.array([ret]))
    print(f"\nSaved → {output_file}")
    rclpy.shutdown()


if __name__ == "__main__":
    main()
