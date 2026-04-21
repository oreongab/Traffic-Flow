"""
YOLO Vehicle Detector — wraps YOLOv8/YOLO11 for vehicle detection from SUMO CCTV frames.
"""

import os
import numpy as np

# Process-wide status flag so /api/system/health can surface the real AI state
# instead of silently returning zero counts from every camera.
YOLO_STATUS: dict[str, object] = {
    "available": False,
    "reason": "not loaded yet",
    "model_path": "",
}

# Vehicle classes in COCO dataset
VEHICLE_CLASSES = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

VEHICLE_CLASS_IDS = list(VEHICLE_CLASSES.keys())


class YOLODetector:
    """YOLO-based vehicle detector."""

    def __init__(self, model_path=None, confidence=0.25):
        from config import Config
        self.model_path = model_path or Config.YOLO_MODEL_PATH
        self.confidence = confidence or Config.YOLO_CONFIDENCE
        self.model = None
        self._load_model()

    def _load_model(self):
        """Load YOLO model. Downloads if not found."""
        try:
            from ultralytics import YOLO
            if os.path.exists(self.model_path):
                self.model = YOLO(self.model_path)
            else:
                # Auto-download YOLOv8n
                self.model = YOLO("yolov8n.pt")
            print(f"✓ YOLO model loaded: {self.model_path}")
            YOLO_STATUS.update({"available": True, "reason": "loaded", "model_path": str(self.model_path)})
        except ImportError:
            banner = (
                "\n" + "=" * 68 + "\n"
                "  ⚠  ultralytics is NOT installed — YOLO detection is DISABLED.\n"
                "      Every camera will report 0 vehicles and confidence_avg = 0.\n"
                "      Fix:  python -m pip install ultralytics\n"
                + "=" * 68 + "\n"
            )
            print(banner)
            YOLO_STATUS.update({"available": False, "reason": "ultralytics not installed", "model_path": str(self.model_path)})
            self.model = None
        except Exception as e:
            print(f"⚠ YOLO model load failed: {e}")
            YOLO_STATUS.update({"available": False, "reason": f"load failed: {e}", "model_path": str(self.model_path)})
            self.model = None

    def detect(self, frame):
        """
        Detect vehicles in a frame.

        Args:
            frame: numpy array (BGR image) or bytes (JPEG)

        Returns:
            list of dict: [{"class": "car", "confidence": 0.85, "bbox": [x1,y1,x2,y2]}, ...]
        """
        if self.model is None:
            return []

        # Convert bytes to numpy if needed
        if isinstance(frame, bytes):
            nparr = np.frombuffer(frame, np.uint8)
            import cv2
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                return []

        results = self.model(
            frame,
            conf=self.confidence,
            classes=VEHICLE_CLASS_IDS,
            verbose=False,
        )

        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())
                bbox = boxes.xyxy[i].tolist()
                detections.append({
                    "class": VEHICLE_CLASSES.get(cls_id, "unknown"),
                    "class_id": cls_id,
                    "confidence": round(conf, 3),
                    "bbox": [round(v, 1) for v in bbox],
                })

        return detections

    def count_vehicles(self, frame):
        """
        Count vehicles by type in a frame.

        Returns:
            dict: {"car": 5, "motorcycle": 2, "bus": 1, "truck": 0, "total": 8}
        """
        detections = self.detect(frame)
        counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
        for d in detections:
            vclass = d["class"]
            if vclass in counts:
                counts[vclass] += 1
        counts["total"] = sum(counts.values())
        return counts

    def detect_and_annotate(self, frame):
        """
        Detect vehicles and draw bounding boxes on the frame.

        Returns:
            tuple: (annotated_frame_bytes, detections_list)
        """
        import cv2

        if isinstance(frame, bytes):
            nparr = np.frombuffer(frame, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is None:
                return b"", []

        detections = self.detect(frame)

        color_map = {
            "car": (0, 255, 0),
            "motorcycle": (255, 255, 0),
            "bus": (255, 128, 0),
            "truck": (0, 128, 255),
        }

        for d in detections:
            x1, y1, x2, y2 = [int(v) for v in d["bbox"]]
            color = color_map.get(d["class"], (255, 255, 255))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f'{d["class"]} {d["confidence"]:.0%}'
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(frame, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        # Add total count overlay
        counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
        for d in detections:
            if d["class"] in counts:
                counts[d["class"]] += 1
        total = sum(counts.values())
        cv2.putText(frame, f"Vehicles: {total}", (20, frame.shape[0] - 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return (buf.tobytes() if ok else b""), detections


# Module-level singleton. Warmed up at app bootstrap so the first CCTV stream
# request doesn't pay the model-load latency spike.
_SHARED_DETECTOR: "YOLODetector | None" = None


def get_shared_detector() -> "YOLODetector | None":
    """Return the process-wide YOLODetector, loading on first call."""
    global _SHARED_DETECTOR
    if _SHARED_DETECTOR is None:
        _SHARED_DETECTOR = YOLODetector()
    return _SHARED_DETECTOR if _SHARED_DETECTOR.model is not None else None
