"""
compute_homography.py — Compute Homography Matrix
==================================================
Usage:
    python3 compute_homography.py [--sensor-id <id>]

Loads  ./resources/<sensor_id>/homography_points.npz
Saves  ./resources/<sensor_id>/homography.npz
"""

import argparse
from pathlib import Path
import numpy as np
import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_ROOT = PROJECT_ROOT / "resources"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sensor-id", default="default")
    args = parser.parse_args()

    resources_dir = RESOURCES_ROOT / args.sensor_id
    pts_path      = resources_dir / "homography_points.npz"
    out_path      = resources_dir / "homography.npz"

    if not pts_path.exists():
        print(f"ERROR: {pts_path} not found. Run homography_collect.py first.")
        return

    data         = np.load(str(pts_path))
    pixel_points = data["pixel_points"].astype(np.float32)
    robot_points = data["robot_points"].astype(np.float32)
    N            = len(pixel_points)

    print(f"Loaded {N} points from {pts_path}")
    if N < 4:
        print("ERROR: Need at least 4 points.")
        return

    src = pixel_points.reshape(-1, 1, 2)
    dst = robot_points.reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC,
                                  ransacReprojThreshold=0.01)

    if H is None:
        print("ERROR: Homography computation failed.")
        return

    inliers = int(mask.sum())
    print(f"Inliers: {inliers}/{N}")

    errors = []
    print(f"\n{'#':>3} {'pixel':>14} {'actual':>18} {'predicted':>18} "
          f"{'err(mm)':>10} {'inlier':>7}")
    print("-" * 80)

    for i in range(N):
        pt   = np.array([[[pixel_points[i, 0], pixel_points[i, 1]]]], dtype=np.float32)
        pred = cv2.perspectiveTransform(pt, H)[0][0]
        err  = float(np.linalg.norm(pred - robot_points[i])) * 1000
        errors.append(err)
        print(f"{i+1:>3} ({pixel_points[i,0]:>5.0f},{pixel_points[i,1]:>5.0f}) "
              f"({robot_points[i,0]:>7.4f},{robot_points[i,1]:>7.4f}) "
              f"({pred[0]:>7.4f},{pred[1]:>7.4f}) "
              f"{err:>9.1f} {'YES' if mask[i] else 'NO ':>7}")

    mean_e = float(np.mean(errors))
    print("-" * 80)
    print(f"Mean: {mean_e:.1f}mm  Median: {float(np.median(errors)):.1f}mm  "
          f"Max: {float(np.max(errors)):.1f}mm")

    if mean_e < 10:
        print("Quality: GOOD")
    elif mean_e < 25:
        print("Quality: ACCEPTABLE")
    else:
        print("Quality: POOR — recollect points")

    np.savez(str(out_path), H=H)
    print(f"\nSaved → {out_path}")
    print(f"\nH =\n{H}")


if __name__ == "__main__":
    main()
