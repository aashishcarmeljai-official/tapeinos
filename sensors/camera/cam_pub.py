"""
cam_pub.py — ROS2 Camera Publisher
====================================
Publishes webcam frames to /video_frames at 10 Hz.

Usage:
    python3 cam_pub.py [--camera-index 0]
"""

import argparse

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2


class ImagePublisher(Node):
    def __init__(self, camera_index: int = 0):
        super().__init__('image_publisher')

        self.publisher_ = self.create_publisher(Image, 'video_frames', 10)

        timer_period = 0.1   # 10 Hz
        self.timer = self.create_timer(timer_period, self.timer_callback)

        self.cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)

        if not self.cap.isOpened():
            self.get_logger().error(f'Failed to open camera index {camera_index}!')
            raise RuntimeError(f'Cannot open camera {camera_index}')

        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        # Warm-up
        for _ in range(5):
            self.cap.read()

        self.br          = CvBridge()
        self.frame_count = 0

        self.get_logger().info(
            f'Camera publisher started — index={camera_index}, topic=/video_frames')

    def timer_callback(self):
        ret, frame = self.cap.read()
        if ret:
            msg = self.br.cv2_to_imgmsg(frame, encoding='bgr8')
            self.publisher_.publish(msg)
            self.frame_count += 1
            if self.frame_count % 50 == 0:
                self.get_logger().info(f'Publishing frame {self.frame_count}')
        else:
            self.get_logger().warn('Failed to capture frame')

    def destroy_node(self):
        if hasattr(self, 'cap') and self.cap.isOpened():
            self.cap.release()
            self.get_logger().info('Camera released')
        super().destroy_node()


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera-index', type=int, default=0,
                        help='OpenCV camera device index (default: 0)')
    parsed, ros_args = parser.parse_known_args(args)

    rclpy.init(args=ros_args)
    node = ImagePublisher(camera_index=parsed.camera_index)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()