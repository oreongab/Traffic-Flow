"""Render synthetic CCTV frames — top-down satellite analytics view.

Replaced the 3D perspective projection (which distorted roads badly) with
a clean top-down view that preserves real road geometry.  The result looks
like a dark satellite / analytics overlay — immediately readable, with
clear road segments, lane markings, and vehicle markers.

All CCTV-style HUD overlays (camera name, GPS, timestamp, vehicle counts,
detection bounding boxes) are preserved.
"""

import math
import re
import time
import threading

import cv2
import numpy as np

# ── Constants ──
DEFAULT_RADIUS = 28.0           # meters — wider view for top-down readability
FRAME_W, FRAME_H = 1280, 720   # output CCTV image size

# Dark analytics palette
BG_COLOR = (18, 22, 28)
ROAD_COLOR = (58, 64, 72)
ROAD_BORDER_COLOR = (88, 95, 105)
LANE_MARK_COLOR = (200, 205, 210)
SIDEWALK_COLOR = (42, 46, 52)
HUD_ACCENT = (44, 214, 255)       # cyan
HUD_WARNING = (0, 72, 255)        # red-ish (BGR)
HUD_GREEN = (80, 220, 100)
GRID_COLOR = (30, 34, 40)
CROSSHAIR_COLOR = (44, 214, 255)

# Rendering scale
LANE_WIDTH_M = 3.5

# Vivid, easily distinguishable vehicle colors (BGR for OpenCV)
VEHICLE_COLORS = {
    "car":        (50, 205, 50),    # bright green
    "motorcycle": (0,  255, 255),   # bright yellow
    "bus":        (20, 130, 255),   # vivid orange
    "truck":      (255, 100, 50),   # blue
    "default":    (180, 180, 180),  # grey
}
VEHICLE_OUTLINE = {
    "car":        (20, 120, 20),
    "motorcycle": (0,  180, 180),
    "bus":        (10, 80, 180),
    "truck":      (180, 60, 20),
    "default":    (100, 100, 100),
}
# Real-world vehicle sizes (length, width) in meters
VEHICLE_SIZES_M = {
    "car":        (4.5, 1.9),
    "motorcycle": (2.2, 0.9),
    "bus":        (12.0, 2.6),
    "truck":      (10.0, 2.6),
    "default":    (4.5, 1.9),
}
# Short labels drawn on vehicle body
VEHICLE_LABELS = {
    "car":        "C",
    "motorcycle": "M",
    "bus":        "BUS",
    "truck":      "TRK",
    "default":    "",
}

# ── Globals (populated on first use) ──
_net = None
_net_lock = threading.Lock()

_TEXT_REPLACEMENTS = {
    "แยกปทุมวัน": "Pathumwan Junction",
    "แยกราชประสงค์": "Ratchaprasong Junction",
    "แยกเฉลิมเผ่า": "Chaloem Phao Junction",
    "แยกพงษ์พระราม": "Phong Phra Ram Junction",
    "แยกเจริญผล": "Charoen Phon Junction",
    "แยกเพลินจิต": "Phloen Chit Junction",
    "แยกชิดลม": "Chit Lom Junction",
    "แยกสารสิน": "Sarasin Junction",
    "แยกจรัสเมือง": "Charat Mueang Junction",
    "แยกสามย่าน": "Sam Yan Junction",
    "แยกศาลาแดง": "Sala Daeng Junction",
    "แยกอังรีดูนังต์": "Henri Dunant Junction",
    "แยกสะพานเหลือง": "Saphan Lueang Junction",
    "แยกวิทยุ": "Witthayu Junction",
    "แยกประตูน้ำ": "Pratunam Junction",
    "แยกอุรุพงษ์": "Uruphong Junction",
    "ถนนพระรามที่ 1": "Rama I Road",
    "ถนนพระรามที่ 4": "Rama IV Road",
    "ถนนพญาไท": "Phaya Thai Road",
    "ถนนราชดำริ": "Ratchadamri Road",
    "ถนนเพลินจิต": "Phloen Chit Road",
    "ถนนบรรทัดทอง": "Banthat Thong Road",
    "ถนนเจริญเมือง": "Charoen Mueang Road",
    "ถนนวิทยุ": "Witthayu Road",
    "ถนนอังรีดูนังต์": "Henri Dunant Road",
    "ถนนสารสิน": "Sarasin Road",
    "ถนนเพชรบุรี": "Phetchaburi Road",
}


def _hud_text(value, fallback=""):
    text = str(value or "").strip()
    if not text:
        return fallback
    for source, replacement in _TEXT_REPLACEMENTS.items():
        text = text.replace(source, replacement)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def _load_network(net_file):
    """Lazy-load the SUMO network once."""
    global _net
    with _net_lock:
        if _net is None:
            try:
                import sumolib
                _net = sumolib.net.readNet(net_file, withInternal=False)
                print(f"✓ sumolib network loaded: {len(_net.getEdges())} edges")
            except Exception as e:
                print(f"⚠ sumolib load failed: {e}")
                _net = False  # sentinel so we don't retry
    return _net if _net else None


def _classify_vehicle_type(vtype_id: str) -> str:
    """Map SUMO vehicle type ID to simplified class."""
    vt = vtype_id.lower()
    if "bus" in vt:
        return "bus"
    elif "truck" in vt:
        return "truck"
    elif "moto" in vt or "motorcycle" in vt:
        return "motorcycle"
    else:
        return "car"


# ── Coordinate transforms (top-down) ──

def _world_to_pixel(wx, wy, cx, cy, radius, img_w, img_h):
    """Convert SUMO world coords → pixel coords (top-down view)."""
    scale = min(img_w, img_h) / (2 * radius)
    px = int((wx - cx) * scale + img_w / 2)
    py = int((cy - wy) * scale + img_h / 2)  # Y is flipped in SUMO
    return px, py


def _meters_to_pixels(meters: float, radius: float, img_w: int, img_h: int) -> int:
    """Convert a world-space distance to pixel distance."""
    scale = min(img_w, img_h) / (2 * radius)
    return max(1, int(meters * scale))


def _get_nearby_edges(net, cx, cy, radius):
    """Get all edges with any shape point within radius of camera center."""
    edges = []
    for edge in net.getEdges():
        for x, y in edge.getShape():
            if math.hypot(x - cx, y - cy) <= radius * 1.4:
                edges.append(edge)
                break
    return edges


def _estimate_camera_heading(net, cx, cy, radius):
    """Estimate the camera viewing direction from the nearest road segment."""
    if not net:
        return 0.0
    best_heading = 0.0
    best_distance = float("inf")
    for edge in _get_nearby_edges(net, cx, cy, radius * 1.5):
        shape = edge.getShape()
        for (ax, ay), (bx, by) in zip(shape[:-1], shape[1:]):
            seg_len = math.hypot(bx - ax, by - ay)
            if seg_len < 1e-3:
                continue
            # project camera point onto segment
            t = ((cx - ax) * (bx - ax) + (cy - ay) * (by - ay)) / (seg_len * seg_len)
            t = max(0.0, min(1.0, t))
            nx = ax + t * (bx - ax)
            ny = ay + t * (by - ay)
            dist = math.hypot(cx - nx, cy - ny)
            if dist < best_distance:
                best_distance = dist
                best_heading = math.atan2(by - ay, bx - ax)
    return best_heading


# ── Drawing functions ──

def _draw_background(frame, cx, cy, radius):
    """Draw dark satellite-style background with subtle grid."""
    h, w = frame.shape[:2]
    frame[:] = BG_COLOR

    # Subtle radial vignette
    y_coords = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
    x_coords = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    dist_sq = x_coords**2 + y_coords**2
    vignette = 1.0 - 0.3 * np.clip(dist_sq, 0, 1)
    for c in range(3):
        frame[:, :, c] = np.clip(frame[:, :, c].astype(np.float32) * vignette, 0, 255).astype(np.uint8)

    # Grid lines (every ~10 meters)
    scale = min(w, h) / (2 * radius)
    grid_spacing_m = 10.0
    grid_spacing_px = int(grid_spacing_m * scale)
    if grid_spacing_px > 8:
        center_px = w // 2
        center_py = h // 2
        # Vertical lines
        start_x = center_px % grid_spacing_px
        for gx in range(start_x, w, grid_spacing_px):
            cv2.line(frame, (gx, 0), (gx, h), GRID_COLOR, 1)
        # Horizontal lines
        start_y = center_py % grid_spacing_px
        for gy in range(start_y, h, grid_spacing_px):
            cv2.line(frame, (0, gy), (w, gy), GRID_COLOR, 1)

    # Scanline effect (very subtle)
    for row in range(0, h, 4):
        cv2.line(frame, (0, row), (w, row), (BG_COLOR[0] + 4, BG_COLOR[1] + 4, BG_COLOR[2] + 4), 1)


def _draw_roads(frame, nearby_edges, cx, cy, radius):
    """Draw road segments as seen from above — clear, straight, recognizable."""
    img_w, img_h = frame.shape[1], frame.shape[0]

    for edge in nearby_edges:
        shape = edge.getShape()
        if len(shape) < 2:
            continue

        lane_count = max(1, int(edge.getLaneNumber() or 1))
        half_width_m = lane_count * LANE_WIDTH_M * 0.5 + 0.5  # shoulder
        half_width_px = _meters_to_pixels(half_width_m, radius, img_w, img_h)

        # Build polyline of road center
        center_pts = []
        for (sx, sy) in shape:
            px, py = _world_to_pixel(sx, sy, cx, cy, radius, img_w, img_h)
            center_pts.append((px, py))

        if len(center_pts) < 2:
            continue

        # Draw road body (thick polyline for each segment)
        for i in range(len(center_pts) - 1):
            p1 = center_pts[i]
            p2 = center_pts[i + 1]

            # Sidewalk layer (slightly wider)
            cv2.line(frame, p1, p2, SIDEWALK_COLOR, half_width_px * 2 + 6, cv2.LINE_AA)
            # Road border
            cv2.line(frame, p1, p2, ROAD_BORDER_COLOR, half_width_px * 2 + 2, cv2.LINE_AA)
            # Road surface
            cv2.line(frame, p1, p2, ROAD_COLOR, half_width_px * 2, cv2.LINE_AA)

        # Draw lane markings (dashed center lines)
        if lane_count > 1:
            for i in range(len(center_pts) - 1):
                p1 = center_pts[i]
                p2 = center_pts[i + 1]
                seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                if seg_len < 4:
                    continue

                # Draw dashed line along the center
                num_dashes = max(1, int(seg_len / 12))
                for d in range(num_dashes):
                    if d % 2 == 1:
                        continue
                    t1 = d / num_dashes
                    t2 = min(1.0, (d + 0.5) / num_dashes)
                    dp1 = (
                        int(p1[0] + (p2[0] - p1[0]) * t1),
                        int(p1[1] + (p2[1] - p1[1]) * t1),
                    )
                    dp2 = (
                        int(p1[0] + (p2[0] - p1[0]) * t2),
                        int(p1[1] + (p2[1] - p1[1]) * t2),
                    )
                    cv2.line(frame, dp1, dp2, LANE_MARK_COLOR, 1, cv2.LINE_AA)

        # Draw edge lines (solid white on road edges)
        for i in range(len(center_pts) - 1):
            p1 = center_pts[i]
            p2 = center_pts[i + 1]
            seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if seg_len < 2:
                continue
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            norm = math.hypot(dx, dy)
            if norm < 1e-3:
                continue
            nx = -dy / norm * (half_width_px - 2)
            ny = dx / norm * (half_width_px - 2)

            # Left edge
            lp1 = (int(p1[0] + nx), int(p1[1] + ny))
            lp2 = (int(p2[0] + nx), int(p2[1] + ny))
            cv2.line(frame, lp1, lp2, (160, 165, 170), 1, cv2.LINE_AA)

            rp1 = (int(p1[0] - nx), int(p1[1] - ny))
            rp2 = (int(p2[0] - nx), int(p2[1] - ny))
            cv2.line(frame, rp1, rp2, (160, 165, 170), 1, cv2.LINE_AA)

def _draw_junctions(frame, net, cx, cy, radius):
    """Draw junction polygons to fill the gaps between road edge lines."""
    img_w, img_h = frame.shape[1], frame.shape[0]
    
    for node in net.getNodes():
        shape = node.getShape()
        if not shape or len(shape) < 3:
            continue
            
        nx, ny = node.getCoord()
        if math.hypot(nx - cx, ny - cy) > radius * 2.0:
            continue
            
        pts = []
        for x, y in shape:
            px, py = _world_to_pixel(x, y, cx, cy, radius, img_w, img_h)
            pts.append((px, py))
            
        if len(pts) > 2:
            poly = np.array(pts, dtype=np.int32)
            # Sidewalk/border equivalent around intersection
            cv2.polylines(frame, [poly], True, ROAD_BORDER_COLOR, 8, cv2.LINE_AA)
            # Fill road color
            cv2.fillPoly(frame, [poly], ROAD_COLOR, lineType=cv2.LINE_AA)


def _draw_camera_marker(frame, cx, cy, radius, camera_heading):
    """Draw the camera position marker and view cone."""
    img_w, img_h = frame.shape[1], frame.shape[0]
    cam_px, cam_py = img_w // 2, img_h // 2  # camera is always at center

    # View cone (semi-transparent)
    cone_length = _meters_to_pixels(radius * 0.7, radius, img_w, img_h)
    cone_angle = math.radians(60)  # 60° FOV

    dir1_x = int(cam_px + cone_length * math.cos(-(camera_heading - cone_angle / 2)))
    dir1_y = int(cam_py - cone_length * math.sin(-(camera_heading - cone_angle / 2)))
    dir2_x = int(cam_px + cone_length * math.cos(-(camera_heading + cone_angle / 2)))
    dir2_y = int(cam_py - cone_length * math.sin(-(camera_heading + cone_angle / 2)))

    cone_pts = np.array([(cam_px, cam_py), (dir1_x, dir1_y), (dir2_x, dir2_y)], dtype=np.int32)
    overlay = frame.copy()
    cv2.fillPoly(overlay, [cone_pts], (40, 60, 80))
    cv2.addWeighted(overlay, 0.25, frame, 0.75, 0, frame)

    # Camera icon
    cv2.circle(frame, (cam_px, cam_py), 10, HUD_ACCENT, 2, cv2.LINE_AA)
    cv2.circle(frame, (cam_px, cam_py), 4, HUD_ACCENT, -1, cv2.LINE_AA)

    # Crosshair
    cv2.line(frame, (cam_px - 20, cam_py), (cam_px - 8, cam_py), CROSSHAIR_COLOR, 1, cv2.LINE_AA)
    cv2.line(frame, (cam_px + 8, cam_py), (cam_px + 20, cam_py), CROSSHAIR_COLOR, 1, cv2.LINE_AA)
    cv2.line(frame, (cam_px, cam_py - 20), (cam_px, cam_py - 8), CROSSHAIR_COLOR, 1, cv2.LINE_AA)
    cv2.line(frame, (cam_px, cam_py + 8), (cam_px, cam_py + 20), CROSSHAIR_COLOR, 1, cv2.LINE_AA)

    # Range circle
    range_px = _meters_to_pixels(radius, radius, img_w, img_h)
    cv2.circle(frame, (cam_px, cam_py), range_px, (40, 50, 60), 1, cv2.LINE_AA)


def _draw_vehicles(frame, vehicles, cx, cy, radius, show_labels=True):
    """Draw vehicles as colored rectangles with direction indicators."""
    img_w, img_h = frame.shape[1], frame.shape[0]
    detection_boxes = []

    for v in vehicles:
        cls = v["class"]
        fill_color = VEHICLE_COLORS.get(cls, VEHICLE_COLORS["default"])
        outline_color = VEHICLE_OUTLINE.get(cls, VEHICLE_OUTLINE["default"])
        size_m = VEHICLE_SIZES_M.get(cls, VEHICLE_SIZES_M["default"])

        # Vehicle heading in radians (SUMO angle: 0=North, clockwise)
        heading_rad = math.radians(90.0 - float(v["angle"]))
        cos_h = math.cos(heading_rad)
        sin_h = math.sin(heading_rad)

        half_len = size_m[0] * 0.5
        half_wid = size_m[1] * 0.5

        # Four corners in world space
        forward = (cos_h, sin_h)
        side = (-sin_h, cos_h)

        corners_world = [
            (v["x"] + forward[0] * half_len + side[0] * half_wid,
             v["y"] + forward[1] * half_len + side[1] * half_wid),
            (v["x"] + forward[0] * half_len - side[0] * half_wid,
             v["y"] + forward[1] * half_len - side[1] * half_wid),
            (v["x"] - forward[0] * half_len - side[0] * half_wid,
             v["y"] - forward[1] * half_len - side[1] * half_wid),
            (v["x"] - forward[0] * half_len + side[0] * half_wid,
             v["y"] - forward[1] * half_len + side[1] * half_wid),
        ]

        # Project to pixels
        corners_px = []
        for wx, wy in corners_world:
            px, py = _world_to_pixel(wx, wy, cx, cy, radius, img_w, img_h)
            corners_px.append((px, py))

        poly = np.array(corners_px, dtype=np.int32)

        # Shadow
        shadow_poly = poly.copy()
        shadow_poly[:, 0] += 2
        shadow_poly[:, 1] += 2
        cv2.fillConvexPoly(frame, shadow_poly, (10, 12, 16), lineType=cv2.LINE_AA)

        # Vehicle body
        cv2.fillConvexPoly(frame, poly, fill_color, lineType=cv2.LINE_AA)
        cv2.polylines(frame, [poly], True, outline_color, 1, cv2.LINE_AA)

        # Center point
        center_px, center_py = _world_to_pixel(v["x"], v["y"], cx, cy, radius, img_w, img_h)

        # Direction arrow
        arrow_len = _meters_to_pixels(size_m[0] * 0.8, radius, img_w, img_h)
        tip_px = int(center_px + arrow_len * cos_h)
        tip_py = int(center_py - arrow_len * sin_h)  # Y flipped
        cv2.arrowedLine(frame, (center_px, center_py), (tip_px, tip_py),
                        (255, 255, 255), 1, cv2.LINE_AA, 0, 0.35)

        if show_labels:
            label = VEHICLE_LABELS.get(cls, "")
            if label:
                font_scale = 0.32 if len(label) <= 1 else 0.26
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
                cv2.putText(frame, label, (center_px - tw // 2, center_py + th // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), 1, cv2.LINE_AA)

            # Speed label
            spd_txt = f"{v['speed']:.0f}"
            cv2.putText(frame, spd_txt, (center_px + 6, center_py - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, (214, 220, 228), 1, cv2.LINE_AA)

        # Build detection box for overlay
        x1 = max(0, int(poly[:, 0].min()) - 4)
        y1 = max(0, int(poly[:, 1].min()) - 4)
        x2 = min(img_w - 1, int(poly[:, 0].max()) + 4)
        y2 = min(img_h - 1, int(poly[:, 1].max()) + 4)
        detection_boxes.append({
            "class": cls,
            "confidence": round(0.78 + 0.18 * (1 - v["distance"] / radius), 2),
            "bbox": [x1, y1, x2, y2],
            "speed": v["speed"],
        })

    return detection_boxes


def _draw_detection_overlay(frame, detection_boxes, detector=None, show_labels=True):
    """Draw YOLO-style detection bounding boxes."""
    # If a real YOLO detector is provided, try to use it
    if detector and detector.model is not None:
        try:
            real_detections = detector.detect(frame)
            if real_detections:
                detection_boxes = real_detections
        except Exception:
            pass  # Fall back to TraCI-based detections

    det_color_map = {
        "car": (0, 255, 0),
        "motorcycle": (255, 255, 0),
        "bus": (255, 128, 0),
        "truck": (0, 128, 255),
    }
    for det in detection_boxes:
        x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
        col = det_color_map.get(det["class"], (255, 255, 255))

        # Corner brackets instead of full rectangle for cleaner look
        bracket_len = max(6, min(16, (x2 - x1) // 4))
        # Top-left
        cv2.line(frame, (x1, y1), (x1 + bracket_len, y1), col, 2)
        cv2.line(frame, (x1, y1), (x1, y1 + bracket_len), col, 2)
        # Top-right
        cv2.line(frame, (x2, y1), (x2 - bracket_len, y1), col, 2)
        cv2.line(frame, (x2, y1), (x2, y1 + bracket_len), col, 2)
        # Bottom-left
        cv2.line(frame, (x1, y2), (x1 + bracket_len, y2), col, 2)
        cv2.line(frame, (x1, y2), (x1, y2 - bracket_len), col, 2)
        # Bottom-right
        cv2.line(frame, (x2, y2), (x2 - bracket_len, y2), col, 2)
        cv2.line(frame, (x2, y2), (x2, y2 - bracket_len), col, 2)

        if show_labels:
            label = f'{det["class"]} {det["confidence"]:.0%}'
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
            cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), col, -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1)


def _draw_cctv_overlay(frame, camera, vehicles, camera_heading, radius):
    """Draw CCTV overlay: camera metadata, GPS anchor, analytics HUD."""
    h, w = frame.shape[:2]
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    raw_name = camera.get("display_name") or camera.get("name") or camera.get("junction") or "CCTV"
    raw_junction = camera.get("junction") or ""
    cam_name = _hud_text(raw_name, "CCTV Camera")
    cam_id = str(camera.get("camera_id", camera.get("id", "")))
    road_name = _hud_text(camera.get("road"), "Unknown road")
    junction_name = _hud_text(raw_junction, "")
    lat = camera.get("lat")
    lng = camera.get("lng")
    total_vehicles = len(vehicles)
    heading_deg = (math.degrees(camera_heading) + 360.0) % 360.0

    # Count by type
    type_counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
    for v in vehicles:
        cls = v.get("class", "car")
        if cls in type_counts:
            type_counts[cls] += 1

    # ── Semi-transparent header bar ──
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 68), (0, 0, 0), -1)
    cv2.rectangle(overlay, (0, h - 108), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    # ── Top bar ──
    # Camera name (left)
    cam_title = cam_name if cam_name.upper().startswith("CCTV") else f"CCTV {cam_name}"
    cv2.putText(frame, cam_title[:54], (16, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, f"ID {cam_id[:34]}  |  {road_name}"[:78], (16, 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, HUD_ACCENT, 1, cv2.LINE_AA)
    if junction_name:
        cv2.putText(frame, f"Junction: {junction_name}"[:78], (16, 64),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (218, 223, 229), 1, cv2.LINE_AA)

    # Timestamp (right)
    (tw, _), _ = cv2.getTextSize(timestamp, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    cv2.putText(frame, timestamp, (w - tw - 16, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    # REC indicator
    cv2.circle(frame, (w - tw - 36, 19), 5, HUD_WARNING, -1)
    cv2.putText(frame, "REC", (w - tw - 16, 46),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, HUD_WARNING, 1, cv2.LINE_AA)

    # ── Bottom bar ──
    # GPS coordinates
    gps_txt = "GPS --, --"
    if lat is not None and lng is not None:
        gps_txt = f"GPS {float(lat):.6f}, {float(lng):.6f}"
    cv2.putText(frame, gps_txt, (16, h - 86),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 234, 239), 1, cv2.LINE_AA)

    # Range and heading
    cv2.putText(frame, f"RANGE {radius:.0f}m  |  HDG {heading_deg:05.1f}", (16, h - 66),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, HUD_ACCENT, 1, cv2.LINE_AA)

    # Vehicle count summary
    cv2.putText(frame, f"TRACKS {total_vehicles:02d}", (16, h - 44),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)

    # Type breakdown
    type_str = f"C:{type_counts['car']}  M:{type_counts['motorcycle']}  B:{type_counts['bus']}  T:{type_counts['truck']}"
    cv2.putText(frame, type_str, (16, h - 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, HUD_GREEN, 1, cv2.LINE_AA)

    # ── Status box (bottom-right) ──
    box_w = 240
    cv2.rectangle(frame, (w - box_w - 16, h - 78), (w - 16, h - 16), (12, 16, 22), -1)
    cv2.rectangle(frame, (w - box_w - 16, h - 78), (w - 16, h - 16), (52, 62, 78), 1)
    cv2.putText(frame, "ANALYTICS VIEW", (w - box_w - 2, h - 58),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, HUD_ACCENT, 1, cv2.LINE_AA)
    cv2.putText(frame, "Top-down satellite overlay", (w - box_w - 2, h - 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, (214, 220, 228), 1, cv2.LINE_AA)

    # View mode tag
    tag = "TraffixFlow CCTV"
    (tw2, _), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    cv2.putText(frame, tag, (w - tw2 - 20, h - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 126, 132), 1, cv2.LINE_AA)


# ── Public API ──

def get_vehicles_near_camera(traci_mod, camera, radius=DEFAULT_RADIUS):
    """
    Get all vehicles within `radius` meters of a camera position.

    Returns a list of dicts:
    [{"id", "type", "class", "speed", "x", "y", "lat", "lng", "angle", "distance"}, ...]
    """
    from utils import sumo_xy_to_latlng

    cx, cy = float(camera["x"]), float(camera["y"])
    vehicles = []

    try:
        for vid in traci_mod.vehicle.getIDList():
            vx, vy = traci_mod.vehicle.getPosition(vid)
            dist = math.hypot(vx - cx, vy - cy)
            if dist <= radius:
                vtype = traci_mod.vehicle.getTypeID(vid)
                speed = traci_mod.vehicle.getSpeed(vid)
                angle = traci_mod.vehicle.getAngle(vid)
                lat, lng = sumo_xy_to_latlng(vx, vy)
                vehicles.append({
                    "id": vid,
                    "type": vtype,
                    "class": _classify_vehicle_type(vtype),
                    "speed": round(speed * 3.6, 1),
                    "x": vx, "y": vy,
                    "lat": lat, "lng": lng,
                    "angle": angle,
                    "distance": round(dist, 1),
                })
    except Exception:
        pass

    return vehicles


def count_vehicles_near_camera(traci_mod, camera, radius=DEFAULT_RADIUS):
    """Count vehicles by type near a camera. Returns {car, motorcycle, bus, truck, total}."""
    vehicles = get_vehicles_near_camera(traci_mod, camera, radius)
    counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
    for v in vehicles:
        cls = v["class"]
        if cls in counts:
            counts[cls] += 1
    counts["total"] = sum(counts.values())
    return counts


def render_cctv_frame(net_file, traci_mod, camera, radius=DEFAULT_RADIUS,
                      show_detection=False, detector=None, show_hud=True,
                      show_camera_marker=True, show_detection_labels=True,
                      show_vehicle_labels=True):
    """
    Render a CCTV frame for a single camera (top-down satellite analytics view).

    Returns JPEG bytes of the rendered frame.
    """
    net = _load_network(net_file)
    cx, cy = float(camera["x"]), float(camera["y"])

    # ── Create frame ──
    frame = np.full((FRAME_H, FRAME_W, 3), BG_COLOR, dtype=np.uint8)
    _draw_background(frame, cx, cy, radius)

    # ── Draw roads ──
    nearby_edges = _get_nearby_edges(net, cx, cy, radius) if net else []
    camera_heading = _estimate_camera_heading(net, cx, cy, radius) if net else 0.0

    if nearby_edges:
        _draw_roads(frame, nearby_edges, cx, cy, radius)
    if net:
        _draw_junctions(frame, net, cx, cy, radius)

    # ── Draw camera marker ──
    if show_camera_marker:
        _draw_camera_marker(frame, cx, cy, radius, camera_heading)

    # ── Draw vehicles ──
    vehicles = get_vehicles_near_camera(traci_mod, camera, radius)
    detection_boxes = _draw_vehicles(frame, vehicles, cx, cy, radius, show_labels=show_vehicle_labels)

    # ── YOLO detection overlay ──
    if show_detection:
        _draw_detection_overlay(frame, detection_boxes, detector, show_labels=show_detection_labels)

    # ── HUD Overlays ──
    if show_hud:
        _draw_cctv_overlay(frame, camera, vehicles, camera_heading, radius)

    # ── Encode to JPEG ──
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else b""


def render_placeholder(text="Camera starting..."):
    """Render a placeholder CCTV frame when simulation is not ready."""
    frame = np.full((FRAME_H, FRAME_W, 3), BG_COLOR, dtype=np.uint8)
    _draw_background(frame, 0, 0, DEFAULT_RADIUS)

    cv2.putText(frame, "TraffixFlow CCTV", (40, 86),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, HUD_ACCENT, 3, cv2.LINE_AA)
    cv2.putText(frame, text[:50], (40, FRAME_H // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (220, 226, 234), 2, cv2.LINE_AA)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(frame, timestamp, (FRAME_W - 340, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (150, 160, 170), 1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""
