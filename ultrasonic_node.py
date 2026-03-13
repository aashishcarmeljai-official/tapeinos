#!/usr/bin/env python3
"""
ultrasonic_node.py — Tapeinos Ultrasonic Sensor Module
=======================================================
Two usage modes:

  1. IMPORTED by app.py (normal path):
       from ultrasonic_node import UltrasonicRunner
       runner = UltrasonicRunner(sensor_id='us_1', port='/dev/ttyUSB0')
       runner.start()          # starts serial + ROS2 node
       runner.set_stop_threshold(20.0)   # cm — enables stopping sensor
       runner.clear_stop_threshold()     # disables stopping sensor
       runner.stop()           # clean shutdown

  2. STANDALONE CLI:
       python3 ultrasonic_node.py --port /dev/ttyUSB0 [--baudrate 115200]

ROS2 topics published:
  /tapeinos/ultrasonic/<sensor_id>/distance   (std_msgs/Float32)
  /tapeinos/ultrasonic/<sensor_id>/stop       (std_msgs/Bool)  — only when threshold set
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from typing import Optional, Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Serial port scanner  (used by Flask route GET /sensors/ports)
# ---------------------------------------------------------------------------

def scan_serial_ports() -> list[dict]:
    """
    Return a list of available serial ports as:
      [{"port": "/dev/ttyUSB0", "description": "...", "hwid": "..."}]
    Works on Linux, macOS, Windows.
    """
    try:
        import serial.tools.list_ports
        results = []
        for p in serial.tools.list_ports.comports():
            results.append({
                "port":        p.device,
                "description": p.description or "",
                "hwid":        p.hwid or "",
            })
        return sorted(results, key=lambda x: x["port"])
    except ImportError:
        log.error("pyserial not installed — cannot scan ports")
        return []
    except Exception as exc:
        log.error(f"Port scan error: {exc}")
        return []


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class UltrasonicNode:
    """
    Thin wrapper around a rclpy Node that:
      - reads distance from a serial port (Arduino sketch sends plain floats)
      - publishes Float32 on /tapeinos/ultrasonic/<id>/distance
      - publishes Bool  on /tapeinos/ultrasonic/<id>/stop when threshold is set
        and distance <= threshold
    """

    def __init__(
        self,
        sensor_id: str,
        port: str,
        baudrate: int = 115200,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.sensor_id = sensor_id
        self.port      = port
        self.baudrate  = baudrate
        self._log_cb   = log_cb or (lambda msg: log.info(msg))

        # ROS2 objects — created in start()
        self._node      = None
        self._pub_dist  = None
        self._pub_stop  = None

        # Serial
        self._ser       = None

        # Stopping sensor
        self._threshold: Optional[float] = None
        self._thresh_lock = threading.Lock()
        self._stop_active = False   # True while stop signal is being published

        # Thread control
        self._running   = False
        self._read_thread: Optional[threading.Thread] = None
        self._owns_rclpy = False  # True if THIS instance called rclpy.init()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open serial port, create ROS2 node, start read loop."""
        import rclpy
        from std_msgs.msg import Float32, Bool

        # Initialise rclpy only if no context is active yet.
        # If JogRunner (or anything else) already called rclpy.init(),
        # we reuse that context rather than calling init() again.
        try:
            if not rclpy.ok():
                rclpy.init()
                self._owns_rclpy = True
                self._log("[rclpy] initialised ✓")
            else:
                self._log("[rclpy] context already active — reusing ✓")
        except Exception as exc:
            self._log(f"[error] rclpy.init() failed: {exc}")
            raise

        self._log("connecting to serial port…")
        try:
            import serial
            self._ser = serial.Serial(self.port, self.baudrate, timeout=1)
            self._log(f"serial connected: {self.port} @ {self.baudrate} baud ✓")
        except Exception as exc:
            self._log(f"[error] serial open failed: {exc}")
            raise

        node_name = f"ultrasonic_{self.sensor_id}"
        self._node     = rclpy.create_node(node_name)
        self._pub_dist = self._node.create_publisher(
            Float32,
            f"/tapeinos/ultrasonic/{self.sensor_id}/distance",
            10,
        )
        self._pub_stop = self._node.create_publisher(
            Bool,
            f"/tapeinos/ultrasonic/{self.sensor_id}/stop",
            10,
        )
        self._log(f"ROS2 node '{node_name}' started ✓")
        self._log(f"publishing → /tapeinos/ultrasonic/{self.sensor_id}/distance")

        self._running = True
        self._read_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name=f"ultrasonic-read-{self.sensor_id}",
        )
        self._read_thread.start()

    def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        if self._read_thread:
            self._read_thread.join(timeout=3.0)
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        if self._node:
            try:
                self._node.destroy_node()
            except Exception:
                pass
        # Only shut down rclpy if this instance was the one that initialised it.
        # If JogRunner owns the context, leave it running.
        if self._owns_rclpy:
            try:
                import rclpy
                rclpy.shutdown()
            except Exception:
                pass
            self._owns_rclpy = False
        self._log("[stopped]")

    # ------------------------------------------------------------------
    # Stopping sensor API
    # ------------------------------------------------------------------

    def set_stop_threshold(self, threshold_cm: float) -> None:
        with self._thresh_lock:
            self._threshold = float(threshold_cm)
        self._log(
            f"[stopping sensor] threshold set → {threshold_cm:.1f} cm  "
            f"(topic: /tapeinos/ultrasonic/{self.sensor_id}/stop)"
        )

    def clear_stop_threshold(self) -> None:
        with self._thresh_lock:
            self._threshold = None
            self._stop_active = False
        self._log("[stopping sensor] threshold cleared")

    def get_threshold(self) -> Optional[float]:
        with self._thresh_lock:
            return self._threshold

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        from std_msgs.msg import Float32, Bool
        import rclpy

        while self._running and rclpy.ok():
            try:
                raw = self._ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                distance = float(line)

                # Publish distance
                msg = Float32()
                msg.data = distance
                self._pub_dist.publish(msg)

                # Stopping sensor logic
                with self._thresh_lock:
                    threshold = self._threshold

                if threshold is not None:
                    should_stop = distance <= threshold
                    if should_stop != self._stop_active:
                        self._stop_active = should_stop
                        stop_msg = Bool()
                        stop_msg.data = should_stop
                        self._pub_stop.publish(stop_msg)
                        if should_stop:
                            self._log(
                                f"[STOP] distance {distance:.1f} cm ≤ "
                                f"threshold {threshold:.1f} cm → STOP published"
                            )
                        else:
                            self._log(
                                f"[clear] distance {distance:.1f} cm > "
                                f"threshold {threshold:.1f} cm → CLEAR published"
                            )

            except ValueError:
                pass   # non-numeric line from Arduino — ignore silently
            except Exception as exc:
                if self._running:
                    self._log(f"[warn] read error: {exc}")
            time.sleep(0.05)

    def _log(self, msg: str) -> None:
        self._log_cb(msg)


# ---------------------------------------------------------------------------
# UltrasonicRunner  — lifecycle manager, used by app.py
# ---------------------------------------------------------------------------

class UltrasonicRunner:
    """
    Manages one UltrasonicNode instance.
    Integrates with the Tapeinos log buffer system via log_cb.

    Usage::
        runner = UltrasonicRunner(
            sensor_id = 'us_1',
            port      = '/dev/ttyUSB0',
            baudrate  = 115200,
            log_cb    = lambda line: _append_log('sensor_us_1', line),
        )
        runner.start()
        runner.set_stop_threshold(20.0)
        runner.stop()
    """

    def __init__(
        self,
        sensor_id: str,
        port: str,
        baudrate: int = 115200,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.sensor_id = sensor_id
        self.port      = port
        self.baudrate  = baudrate
        self._log_cb   = log_cb or (lambda m: log.info(m))
        self._node_obj: Optional[UltrasonicNode] = None
        self._lock     = threading.Lock()
        self.running   = False
        self.error:    Optional[str] = None

    def start(self) -> bool:
        """Start the sensor node. Returns True on success."""
        with self._lock:
            if self.running:
                return True
            try:
                self._node_obj = UltrasonicNode(
                    sensor_id = self.sensor_id,
                    port      = self.port,
                    baudrate  = self.baudrate,
                    log_cb    = self._log_cb,
                )
                self._node_obj.start()
                self.running = True
                self.error   = None
                return True
            except Exception as exc:
                self.error   = str(exc)
                self.running = False
                self._log_cb(f"[error] failed to start: {exc}")
                return False

    def stop(self) -> None:
        with self._lock:
            if self._node_obj:
                self._node_obj.stop()
                self._node_obj = None
            self.running = False

    def set_stop_threshold(self, threshold_cm: float) -> None:
        with self._lock:
            if self._node_obj:
                self._node_obj.set_stop_threshold(threshold_cm)
            else:
                self._log_cb("[warn] sensor not running — start it first")

    def clear_stop_threshold(self) -> None:
        with self._lock:
            if self._node_obj:
                self._node_obj.clear_stop_threshold()

    def get_threshold(self) -> Optional[float]:
        with self._lock:
            if self._node_obj:
                return self._node_obj.get_threshold()
            return None

    def get_status(self) -> dict:
        return {
            "running":   self.running,
            "error":     self.error,
            "port":      self.port,
            "baudrate":  self.baudrate,
            "threshold": self.get_threshold(),
        }


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Tapeinos Ultrasonic Node")
    parser.add_argument("--port",     default="/dev/ttyUSB0")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--sensor-id", default="us_0")
    args = parser.parse_args()

    import rclpy
    rclpy.init()

    logging.basicConfig(level=logging.INFO)
    node = UltrasonicNode(
        sensor_id=args.sensor_id,
        port=args.port,
        baudrate=args.baudrate,
        log_cb=lambda m: print(m, flush=True),
    )
    node.start()

    try:
        while rclpy.ok():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        rclpy.shutdown()


if __name__ == "__main__":
    main()