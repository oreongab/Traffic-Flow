"use client";

import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from "react-leaflet";
import L from "leaflet";
import { getVehicles, getTrafficLights, getCameras } from "@/lib/api";
import type { Vehicle, TrafficLight, Camera } from "@/lib/types";
import CctvFeed from "@/components/CctvFeed";
import "leaflet/dist/leaflet.css";

const PATHUMWAN_CENTER: [number, number] = [13.7411, 100.5315];
const LIGHT_TILE = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png";

function vehicleIcon(color: string) {
  const c = color || "#94a3b8";
  return L.divIcon({
    className: "",
    html: `<div style="width:10px;height:10px;border-radius:50%;background:${c};border:1px solid rgba(0,0,0,0.18);box-shadow:0 0 0 2px rgba(255,255,255,0.55);"></div>`,
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  });
}

function lightIcon(state: string) {
  const color = state === "green" ? "#22c55e" : state === "yellow" ? "#eab308" : "#ef4444";
  return L.divIcon({
    className: "",
    html: `<div style="width:14px;height:14px;border-radius:3px;background:${color};border:2px solid #374151;box-shadow:0 0 6px ${color}80;"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

const cameraIcon = L.divIcon({
  className: "",
  html: `<div style="width:22px;height:22px;border-radius:50%;background:#22c55e;border:2px solid #15803d;display:flex;align-items:center;justify-content:center;box-shadow:0 1px 4px rgba(0,0,0,0.25);"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></div>`,
  iconSize: [22, 22],
  iconAnchor: [11, 11],
});

function MapInvalidator() {
  const map = useMap();
  useEffect(() => {
    setTimeout(() => map.invalidateSize(), 200);
  }, [map]);
  return null;
}

function FlyTo({ coords }: { coords: [number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (coords) map.flyTo(coords, 17, { duration: 1.5 });
  }, [map, coords]);
  return null;
}

function FitBounds({ bounds }: { bounds: [[number, number], [number, number]] | null }) {
  const map = useMap();
  useEffect(() => {
    if (!bounds) return;
    const [[minLat, minLng], [maxLat, maxLng]] = bounds;
    const centerLat = (minLat + maxLat) / 2;
    const centerLng = (minLng + maxLng) / 2;
    const spanLat = Math.abs(maxLat - minLat);
    const spanLng = Math.abs(maxLng - minLng);
    const maxSpan = Math.max(spanLat, spanLng);
    // Always zoom IN to the road center. Pick zoom by road span so long roads
    // (พระราม 1/4) still fit but never zoom out past 15.
    let targetZoom = 18;
    if (maxSpan > 0.03) targetZoom = 15;
    else if (maxSpan > 0.015) targetZoom = 16;
    else if (maxSpan > 0.006) targetZoom = 17;
    map.stop();
    map.flyTo([centerLat, centerLng], targetZoom, { duration: 0.8 });
  }, [map, bounds]);
  return null;
}

type RoadLine = {
  road_id: string;
  segments: [number, number][][];
  color: string;
  weight?: number;
};

interface MapViewProps {
  showVehicles?: boolean;
  showCameras?: boolean;
  showLights?: boolean;
  searchCoords?: [number, number] | null;
  onCameraClick?: (camera: Camera) => void;
  cameraPopup?: "info" | "stream";
  roadLines?: RoadLine[];
  activeRoadId?: string | null;
  fitBounds?: [[number, number], [number, number]] | null;
}

export default function MapView({
  showVehicles = true,
  showCameras = true,
  showLights = true,
  searchCoords = null,
  onCameraClick,
  cameraPopup = "info",
  roadLines,
  activeRoadId = null,
  fitBounds = null,
}: MapViewProps) {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [lights, setLights] = useState<TrafficLight[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);

  // Vehicles + lights move in real time — refresh fast.
  useEffect(() => {
    if (!showVehicles && !showLights) return;
    let active = true;

    async function refreshLive() {
      try {
        const [vData, lData] = await Promise.all([
          showVehicles ? getVehicles() : Promise.resolve([]),
          showLights ? getTrafficLights() : Promise.resolve([]),
        ]);
        if (!active) return;
        if (showVehicles) setVehicles(vData as Vehicle[]);
        if (showLights) setLights(lData as TrafficLight[]);
      } catch {
        /* API offline */
      }
    }

    void refreshLive();
    const interval = setInterval(() => void refreshLive(), 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [showVehicles, showLights]);

  // Camera inventory changes rarely — poll much less often so page switches
  // don't wait on a heavy /cameras response every mount.
  useEffect(() => {
    if (!showCameras) return;
    let active = true;

    async function refreshCameras() {
      try {
        const cData = await getCameras();
        if (active) setCameras(cData as Camera[]);
      } catch {
        /* API offline */
      }
    }

    void refreshCameras();
    const interval = setInterval(() => void refreshCameras(), 60000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [showCameras]);

  return (
    <MapContainer center={PATHUMWAN_CENTER} zoom={15} className="h-full w-full" zoomControl={false}>
      <TileLayer attribution='&copy; <a href="https://carto.com/">CARTO</a>' url={LIGHT_TILE} />
      <MapInvalidator />
      <FlyTo coords={searchCoords} />
      <FitBounds bounds={fitBounds} />

      {roadLines?.map((line) =>
        (line.segments || []).map((seg, idx) => (
          <Polyline
            key={`${line.road_id}-${idx}`}
            positions={seg}
            pathOptions={{
              color: line.color,
              weight: line.road_id === activeRoadId ? (line.weight ?? 6) + 2 : (line.weight ?? 6),
              opacity: 0.9,
            }}
          />
        ))
      )}

      {showVehicles && vehicles.map((v) => (
        <Marker key={v.id} position={[v.lat, v.lng]} icon={vehicleIcon(v.color || "#94a3b8")}>
          <Popup><span className="text-xs text-gray-700">{v.type} — {v.speed.toFixed(1)} km/h</span></Popup>
        </Marker>
      ))}

      {showLights && lights.map((l) => (
        <Marker key={l.id} position={[l.lat, l.lng]} icon={lightIcon(l.state)}>
          <Popup><span className="text-xs text-gray-700">{l.junction_name} — {l.state}</span></Popup>
        </Marker>
      ))}

      {showCameras && cameras.map((c, i) => (
        <Marker
          key={c.camera_id || `cam-${i}`}
          position={[c.lat, c.lng]}
          icon={cameraIcon}
          eventHandlers={{
            click: () => {
              onCameraClick?.(c);
            },
          }}
        >
          <Popup maxWidth={420} minWidth={380}>
            <div className="text-xs text-gray-700" style={{ width: 380 }}>
              <div className="mb-2 min-w-0">
                  <p className="font-bold text-sm text-[#1e3a5f] truncate">{c.name}</p>
                  <p className="text-gray-400 text-[10px]">{c.camera_id}</p>
              </div>

              {cameraPopup === "stream" && (
                <div className="w-full overflow-hidden rounded-lg border border-gray-200 bg-slate-950" style={{ height: 200 }}>
                  <CctvFeed
                    cameraId={c.camera_id}
                    cameraName={c.name}
                    subtitle={c.junction || c.road || undefined}
                    cameraLat={c.lat}
                    cameraLng={c.lng}
                  />
                </div>
              )}

              {/* Info grid below stream */}
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                <div><span className="text-gray-400">ถนน:</span> <span className="font-medium text-gray-700">{c.road || "-"}</span></div>
                <div><span className="text-gray-400">แยก:</span> <span className="font-medium text-gray-700">{c.junction || "-"}</span></div>
                <div><span className="text-gray-400">ละติจูด:</span> <span className="font-medium text-gray-700">{c.lat.toFixed(6)}</span></div>
                <div><span className="text-gray-400">ลองจิจูด:</span> <span className="font-medium text-gray-700">{c.lng.toFixed(6)}</span></div>
              </div>
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
