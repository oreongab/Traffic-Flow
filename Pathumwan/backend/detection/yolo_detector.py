"""
YOLO Vehicle Detector — wraps Ultralytics YOLO detection models for SUMO CCTV frames.
"""

import os
import numpy as np

DEFAULT_YOLO_MODEL = "yolo12n.pt"

# Process-wide status flag so /api/system/health can surface the real AI state
# instead of silently returning zero counts from every camera.
YOLO_STATUS: dict[str, object] = {
    "available": False,
    "reason": "not loaded yet",
    "model_path": "",
    "device": "cpu",
    "half": False,
}


def _detect_device() -> tuple[str, bool]:
    """Pick the best available torch device for inference.

    Returns ``(device, use_half)``. ``half`` (FP16) is only enabled on CUDA
    where it is a near-free 2× speedup; on CPU/MPS it is left off because
    the gain is small or negative.
    """
    try:
        import torch  # type: ignore[import-not-found]
    except Exception:
        return "cpu", False
    try:
        if torch.cuda.is_available():
            return "cuda", True
    except Exception:
        pass
    try:
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps", False
    except Exception:
        pass
    return "cpu", False

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
        self.device, self.use_half = _detect_device()
        self.imgsz = int(getattr(Config, "YOLO_IMGSZ", int(os.getenv("YOLO_IMGSZ", 480))))
        self._load_model()

    def _load_model(self):
        """Load the configured YOLO model, preferring YOLO12 defaults."""
        try:
            from ultralytics import YOLO
            errors: list[str] = []
            for source in self._candidate_model_sources():
                try:
                    self.model = YOLO(source)
                    resolved_source = getattr(self.model, "ckpt_path", None) or source
                    # Move to GPU and switch to FP16 when CUDA is available so
                    # 55 cams × YOLO12n is feasible without saturating the CPU.
                    try:
                        self.model.to(self.device)
                        if self.use_half:
                            inner = getattr(self.model, "model", None)
                            if inner is not None and hasattr(inner, "half"):
                                inner.half()
                    except Exception as exc:
                        # Fallback gracefully if half/.to fails — better than
                        # crashing the whole detector.
                        print(f"  ⚠ YOLO device move failed ({self.device}, half={self.use_half}): {exc}")
                        self.device, self.use_half = "cpu", False
                    print(
                        f"✓ YOLO model loaded: {resolved_source} "
                        f"(device={self.device}, half={self.use_half}, imgsz={self.imgsz})"
                    )
                    YOLO_STATUS.update({
                        "available": True,
                        "reason": "loaded",
                        "model_path": str(resolved_source),
                        "device": self.device,
                        "half": self.use_half,
                    })
                    return
                except Exception as exc:
                    errors.append(f"{source}: {exc}")

            raise RuntimeError("; ".join(errors))
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

    def _candidate_model_sources(self) -> list[str]:
        """Return preferred model sources, defaulting to YOLO12n."""
        requested = str(self.model_path or "").strip()
        candidates: list[str] = []

        if requested:
            basename = os.path.basename(requested)
            if os.path.exists(requested):
                candidates.append(requested)
            elif basename == DEFAULT_YOLO_MODEL:
                candidates.append(DEFAULT_YOLO_MODEL)
            elif os.path.sep not in requested and requested.endswith(".pt"):
                candidates.append(requested)

        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fallback_candidates = [
            os.path.join(backend_dir, DEFAULT_YOLO_MODEL),
            os.path.join(os.path.abspath(os.path.dirname(__file__)), "models", DEFAULT_YOLO_MODEL),
            DEFAULT_YOLO_MODEL,
        ]
        for candidate in fallback_candidates:
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

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
        # Dev/test: force blindness to verify optical-flow blindness fallback
        if os.getenv("YOLO_FORCE_BLIND") == "1":
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
            imgsz=self.imgsz,
            half=self.use_half,
            device=self.device,
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
