"""Offline utility to sync camera rows from all traffic lights in osm.net.xml.

This updates the local SQLite database so the API camera inventory matches the
traffic lights in the SUMO network even before the simulator is started.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.camera_sync import sync_cameras_from_network


def main() -> None:
    result = sync_cameras_from_network()
    print(f"active={result.get('active', 0)} inactive={result.get('inactive', 0)}")


if __name__ == "__main__":
    main()