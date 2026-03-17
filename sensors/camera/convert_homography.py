"""
convert_homography.py — Convert homography.npz → homography.txt
================================================================
Usage:
    python3 convert_homography.py [--sensor-id <id>]

Loads  ./resources/<sensor_id>/homography.npz
Saves  ./resources/<sensor_id>/homography.txt
"""

import argparse
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_ROOT = PROJECT_ROOT / "resources"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sensor-id", default="default")
    args = parser.parse_args()

    resources_dir = RESOURCES_ROOT / args.sensor_id
    npz_path      = resources_dir / "homography.npz"
    txt_path      = resources_dir / "homography.txt"

    if not npz_path.exists():
        print(f"ERROR: {npz_path} not found. Run compute_homography.py first.")
        return

    data = np.load(str(npz_path))
    H    = data["H"]
    np.savetxt(str(txt_path), H)

    print(f"Saved → {txt_path}")
    print(f"H =\n{H}")


if __name__ == "__main__":
    main()
