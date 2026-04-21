"""
Utility functions for coordinate conversion and SUMO path discovery.
"""

import os
import math

# --- pyproj for UTM -> WGS84 ---
try:
    from pyproj import Transformer
    _transformer = Transformer.from_crs("EPSG:32647", "EPSG:4326", always_xy=True)
    HAS_PYPROJ = True
except ImportError:
    _transformer = None
    HAS_PYPROJ = False
    print("⚠  pyproj not installed — using approximate coords.  pip install pyproj")

# SUMO network offset constants (Pathumwan area)
NET_OFFSET_X = -659880.59
NET_OFFSET_Y = -1516302.89


def find_sumo_binary():
    """Locate the sumo binary (headless). Falls back to sumo-gui if sumo not found."""
    sumo_home = os.environ.get("SUMO_HOME", "")
    if sumo_home:
        # Prefer headless sumo first
        for name in ("sumo", "sumo-gui"):
            for ext in (".exe", ""):
                p = os.path.join(sumo_home, "bin", name + ext)
                if os.path.exists(p):
                    return p
    return "sumo"


def find_sumo_gui():
    """Locate the sumo-gui executable (for backward compatibility)."""
    sumo_home = os.environ.get("SUMO_HOME", "")
    if sumo_home:
        for ext in (".exe", ""):
            p = os.path.join(sumo_home, "bin", "sumo-gui" + ext)
            if os.path.exists(p):
                return p
    return "sumo-gui"


def sumo_xy_to_latlng(x, y):
    """Convert SUMO internal XY coordinates to WGS84 lat/lng."""
    utm_x = x - NET_OFFSET_X
    utm_y = y - NET_OFFSET_Y
    if HAS_PYPROJ and _transformer is not None:
        lng, lat = _transformer.transform(utm_x, utm_y)
        return round(lat, 6), round(lng, 6)
    else:
        lat = utm_y / 111320.0
        lng = utm_x / (111320.0 * math.cos(math.radians(13.74)))
        return round(lat, 6), round(lng, 6)


def latlng_to_sumo_xy(lat, lng):
    """Convert WGS84 lat/lng to SUMO internal XY coordinates."""
    if HAS_PYPROJ:
        try:
            from pyproj import Transformer
            inv = Transformer.from_crs("EPSG:4326", "EPSG:32647", always_xy=True)
            utm_x, utm_y = inv.transform(lng, lat)
            return utm_x + NET_OFFSET_X, utm_y + NET_OFFSET_Y
        except Exception:
            pass
    # Approximate inverse
    utm_y = lat * 111320.0
    utm_x = lng * (111320.0 * math.cos(math.radians(13.74)))
    return utm_x + NET_OFFSET_X, utm_y + NET_OFFSET_Y
