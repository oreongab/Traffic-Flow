import type { Camera } from "@/lib/types";

export function cameraPrimaryLabel(camera?: Partial<Camera> | null) {
  if (!camera) return "CCTV";
  return camera.display_name || camera.name || camera.road || camera.camera_id || "CCTV";
}

export function cameraSecondaryLabel(camera?: Partial<Camera> | null) {
  if (!camera) return "";
  return (
    camera.location_hint ||
    [camera.junction, camera.road].filter(Boolean).join(" • ") ||
    camera.camera_id ||
    ""
  );
}