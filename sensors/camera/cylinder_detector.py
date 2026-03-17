"""
cylinder_detector.py — Tapeinos Shared Cylinder Detection
==========================================================
3-stage top-face detector used by:
  - homography_collect.py
  - green_tracker.py
  - camera_node.py (action callbacks)

Color is user-specified per sensor. Supported presets:
  "red", "green", "blue", "yellow"

Detection pipeline
------------------
  Stage A — Hough Circle (HOUGH_GRADIENT_ALT) on color-masked ROI
  Stage B — Convex Hull + fitEllipse fallback
  Stage C — Bounding-box centre last resort
"""

from __future__ import annotations
import cv2
import numpy as np

# ── HSV ranges for each supported color ──────────────────────────────────────
COLOR_RANGES: dict[str, list[tuple]] = {
    "red": [
        ((0,   100, 100), (10,  255, 255)),
        ((160, 100, 100), (180, 255, 255)),
    ],
    "green": [
        ((35, 80, 80), (85, 255, 255)),
    ],
    "blue": [
        ((100, 80, 80), (130, 255, 255)),
    ],
    "yellow": [
        ((20, 100, 100), (35, 255, 255)),
    ],
}

# ── Hough tuning constants ────────────────────────────────────────────────────
HOUGH_DP                = 1.2
HOUGH_MIN_DIST          = 50.0
HOUGH_PARAM1            = 100.0
HOUGH_PARAM2            = 0.75
HOUGH_RADIUS_FRACTION   = 0.45
HOUGH_RADIUS_TOLERANCE  = 0.40
HOUGH_CENTRE_GATE       = 0.55
MIN_CONTOUR_AREA        = 100.0


class DetectionResult:
    HOUGH = "hough"
    HULL  = "hull"
    BBOX  = "bbox"

    def __init__(self):
        self.cx       = 0
        self.cy       = 0
        self.radius   = 0.0
        self.axis_a   = 0.0
        self.axis_b   = 0.0
        self.angle    = 0.0
        self.method   = self.BBOX

    def to_dict(self) -> dict:
        return {
            "cx":     self.cx,
            "cy":     self.cy,
            "radius": round(self.radius, 1),
            "axis_a": round(self.axis_a, 1),
            "axis_b": round(self.axis_b, 1),
            "angle":  round(self.angle,  1),
            "method": self.method,
        }


def build_mask(frame: np.ndarray, color: str) -> np.ndarray:
    """Build a binary mask for the given color preset."""
    color = color.lower()
    ranges = COLOR_RANGES.get(color)
    if ranges is None:
        raise ValueError(f"Unknown color '{color}'. "
                         f"Choose from: {list(COLOR_RANGES)}")
    hsv   = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask  = np.zeros(frame.shape[:2], dtype=np.uint8)
    for lo, hi in ranges:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return mask


def detect_cylinder_top(frame: np.ndarray,
                         color: str = "red") -> DetectionResult | None:
    """
    Detect the top-face centre of a cylinder of the given color.

    Returns DetectionResult or None if nothing is found.
    """
    mask = build_mask(frame, color)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < MIN_CONTOUR_AREA:
        return None

    x, y, w, h = cv2.boundingRect(contour)

    # ── Stage A: Hough ───────────────────────────────────────────────────
    pad  = 10
    rx1  = max(0, x - pad)
    ry1  = max(0, y - pad)
    rx2  = min(frame.shape[1], x + w + pad)
    ry2  = min(frame.shape[0], y + h + pad)

    roi_mask = mask[ry1:ry2, rx1:rx2]
    roi_gray = cv2.cvtColor(frame[ry1:ry2, rx1:rx2], cv2.COLOR_BGR2GRAY)
    roi_gray[roi_mask == 0] = 0
    roi_gray = cv2.GaussianBlur(roi_gray, (9, 9), 2.0)

    expected_r = w * HOUGH_RADIUS_FRACTION
    r_min      = max(5, int(expected_r * (1.0 - HOUGH_RADIUS_TOLERANCE)))
    r_max      = int(expected_r * (1.0 + HOUGH_RADIUS_TOLERANCE))

    circles = cv2.HoughCircles(
        roi_gray, cv2.HOUGH_GRADIENT_ALT,
        dp=HOUGH_DP, minDist=HOUGH_MIN_DIST,
        param1=HOUGH_PARAM1, param2=HOUGH_PARAM2,
        minRadius=r_min, maxRadius=r_max,
    )

    bbox_cx_roi = (x - rx1) + w / 2.0
    bbox_cy_roi = (y - ry1) + h / 2.0
    gate_x      = w * HOUGH_CENTRE_GATE
    gate_y      = h * HOUGH_CENTRE_GATE

    if circles is not None:
        for c in circles[0]:
            cx_roi, cy_roi, r = float(c[0]), float(c[1]), float(c[2])
            if (abs(cx_roi - bbox_cx_roi) < gate_x and
                    abs(cy_roi - bbox_cy_roi) < gate_y):
                res        = DetectionResult()
                res.cx     = int(cx_roi + rx1)
                res.cy     = int(cy_roi + ry1)
                res.radius = r
                res.axis_a = r
                res.axis_b = r
                res.angle  = 0.0
                res.method = DetectionResult.HOUGH
                return res

    # ── Stage B: Hull + fitEllipse ───────────────────────────────────────
    hull = cv2.convexHull(contour)
    if len(hull) >= 5:
        ell        = cv2.fitEllipse(hull)
        res        = DetectionResult()
        res.cx     = int(ell[0][0])
        res.cy     = int(ell[0][1])
        res.axis_a = ell[1][0] / 2.0
        res.axis_b = ell[1][1] / 2.0
        res.radius = (res.axis_a + res.axis_b) / 2.0
        res.angle  = ell[2]
        res.method = DetectionResult.HULL
        return res

    # ── Stage C: Bounding box ────────────────────────────────────────────
    res        = DetectionResult()
    res.cx     = x + w // 2
    res.cy     = y + h // 2
    res.radius = w / 2.0
    res.axis_a = w / 2.0
    res.axis_b = h / 2.0
    res.angle  = 0.0
    res.method = DetectionResult.BBOX
    return res


# ── Overlay drawing ───────────────────────────────────────────────────────────
_METHOD_COLORS = {
    DetectionResult.HOUGH: (0, 220,   0),
    DetectionResult.HULL:  (0, 165, 255),
    DetectionResult.BBOX:  (0,   0, 255),
}
_METHOD_TAGS = {
    DetectionResult.HOUGH: "[H]",
    DetectionResult.HULL:  "[E]",
    DetectionResult.BBOX:  "[B]",
}


def draw_detection(frame: np.ndarray,
                   res: DetectionResult,
                   extra_label: str = "") -> None:
    """Draw detection overlay on frame in-place."""
    colour = _METHOD_COLORS.get(res.method, (200, 200, 200))
    tag    = _METHOD_TAGS.get(res.method, "[?]")

    if res.method == DetectionResult.HOUGH:
        cv2.circle(frame, (res.cx, res.cy), int(res.radius), colour, 2)
        cr = int(res.radius)
        cv2.line(frame, (res.cx - cr, res.cy), (res.cx + cr, res.cy), colour, 1)
        cv2.line(frame, (res.cx, res.cy - cr), (res.cx, res.cy + cr), colour, 1)
    else:
        cv2.ellipse(frame, (res.cx, res.cy),
                    (max(1, int(res.axis_a)), max(1, int(res.axis_b))),
                    res.angle, 0, 360, colour, 2)

    cv2.circle(frame, (res.cx, res.cy),  5, colour, -1)
    cv2.circle(frame, (res.cx, res.cy), 16, colour,  1)

    label = (f"{tag} ({res.cx},{res.cy}) r={int(res.radius)}px"
             + (f"  {extra_label}" if extra_label else ""))
    cv2.putText(frame, label, (res.cx + 20, res.cy - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)