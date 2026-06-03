"""
Utility functions for coordinate conversion and SUMO path discovery.
"""

import math
import os
import xml.etree.ElementTree as ET
from functools import lru_cache

from config import Config

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


@lru_cache(maxsize=1)
def _load_net_bounds():
    net_path = os.path.abspath(Config.SUMO_NET_FILE)
    if not os.path.exists(net_path):
        return None

    try:
        root = ET.parse(net_path).getroot()
    except ET.ParseError:
        return None

    location = root.find("location")
    if location is None:
        return None

    try:
        conv_boundary = tuple(float(value) for value in location.get("convBoundary", "").split(","))
        orig_boundary = tuple(float(value) for value in location.get("origBoundary", "").split(","))
    except ValueError:
        return None

    if len(conv_boundary) != 4 or len(orig_boundary) != 4:
        return None

    return {
        "conv": conv_boundary,
        "orig": orig_boundary,
    }


def _sumo_xy_to_latlng_linear(x, y):
    bounds = _load_net_bounds()
    if not bounds:
        return None

    min_x, min_y, max_x, max_y = bounds["conv"]
    min_lng, min_lat, max_lng, max_lat = bounds["orig"]
    width = max_x - min_x
    height = max_y - min_y
    if width == 0 or height == 0:
        return None

    lng = min_lng + ((x - min_x) / width) * (max_lng - min_lng)
    lat = min_lat + ((y - min_y) / height) * (max_lat - min_lat)
    return round(lat, 6), round(lng, 6)


def _latlng_to_sumo_xy_linear(lat, lng):
    bounds = _load_net_bounds()
    if not bounds:
        return None

    min_x, min_y, max_x, max_y = bounds["conv"]
    min_lng, min_lat, max_lng, max_lat = bounds["orig"]
    lng_span = max_lng - min_lng
    lat_span = max_lat - min_lat
    if lng_span == 0 or lat_span == 0:
        return None

    x = min_x + ((lng - min_lng) / lng_span) * (max_x - min_x)
    y = min_y + ((lat - min_lat) / lat_span) * (max_y - min_y)
    return round(x, 2), round(y, 2)


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

    linear = _sumo_xy_to_latlng_linear(x, y)
    if linear is not None:
        return linear

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

    linear = _latlng_to_sumo_xy_linear(lat, lng)
    if linear is not None:
        return linear

    # Approximate inverse
    utm_y = lat * 111320.0
    utm_x = lng * (111320.0 * math.cos(math.radians(13.74)))
    return utm_x + NET_OFFSET_X, utm_y + NET_OFFSET_Y
