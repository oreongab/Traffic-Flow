import type { Camera } from "@/lib/types";

function normalizeLabel(value?: string | null) {
  return String(value || "").trim();
}

function looksLikeMachineLabel(value?: string | null) {
  const label = normalizeLabel(value);
  if (!label) return true;
  return /^(joinedS|cluster_|TLS_|J\d|\d+[_-]\d+)/i.test(label) || (label.includes("_") && /\d/.test(label));
}

function firstHumanReadable(...values: Array<string | undefined | null>) {
  for (const value of values) {
    const label = normalizeLabel(value);
    if (label && !looksLikeMachineLabel(label)) {
      return label;
    }
  }
  return "";
}

export function cameraPrimaryLabel(camera?: Partial<Camera> | null) {
  if (!camera) return "CCTV";
  return (
    firstHumanReadable(camera.display_name, camera.name, camera.junction, camera.road) ||
    normalizeLabel(camera.display_name) ||
    normalizeLabel(camera.name) ||
    normalizeLabel(camera.road) ||
    normalizeLabel(camera.camera_id) ||
    "CCTV"
  );
}

export function cameraSecondaryLabel(camera?: Partial<Camera> | null) {
  if (!camera) return "";
  const locationHint = normalizeLabel(camera.location_hint);
  if (locationHint) {
    return locationHint;
  }

  const parts = [firstHumanReadable(camera.junction), firstHumanReadable(camera.road)].filter(Boolean);
  if (parts.length > 0) {
    return parts.join(" • ");
  }

  const fallbackId = normalizeLabel(camera.camera_id);
  return (
    (fallbackId ? `ID ${fallbackId}` : "") ||
    ""
  );
}