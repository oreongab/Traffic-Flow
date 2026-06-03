"use client";

import { useEffect, useState, useRef, useMemo, useCallback } from "react";
import { Circle, MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import { getCameraVehicles } from "@/lib/api";
import "leaflet/dist/leaflet.css";

/* ─── Types ─── */
interface CameraVehicle {
  id: string;
  class: string;
  speed: number;
  lat: number;
  lng: number;
  angle: number;
  distance: number;
}

interface CctvMiniMapProps {
  cameraId: string;
  cameraName: string;
  lat: number;
  lng: number;
  pollInterval?: number;
  detectMode?: boolean;
  zoom?: number;
  interactive?: boolean;
  showHud?: boolean;
}

/* ─── Vehicle Icon Factory ─── */

const VEHICLE_COLORS: Record<string, string> = {
  car: "#4ade80",
  motorcycle: "#facc15",
  bus: "#fb923c",
  truck: "#60a5fa",
  default: "#a1a1aa",
};

const VEHICLE_SIZES: Record<string, [number, number]> = {
  car: [22, 12],
  motorcycle: [18, 9],
  bus: [30, 14],
  truck: [28, 14],
  default: [22, 12],
};

function makeVehicleIcon(vClass: string, angle: number): L.DivIcon {
  const color = VEHICLE_COLORS[vClass] || VEHICLE_COLORS.default;
  const [w, h] = VEHICLE_SIZES[vClass] || VEHICLE_SIZES.default;
  const rotation = angle || 0;

  let svgBody = "";
  if (vClass === "motorcycle") {
    svgBody = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      <ellipse cx="${w / 2}" cy="${h / 2}" rx="${w / 2 - 1}" ry="${h / 2 - 1}" fill="${color}" stroke="#222" stroke-width="1"/>
      <circle cx="${w / 2}" cy="${h / 2 - 1}" r="2" fill="#fff" opacity="0.6"/>
    </svg>`;
  } else if (vClass === "bus") {
    svgBody = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="${w - 2}" height="${h - 2}" rx="2" ry="2" fill="${color}" stroke="#222" stroke-width="1"/>
      <rect x="3" y="2" width="3" height="3" rx="0.5" fill="#fff" opacity="0.7"/>
      <rect x="7" y="2" width="3" height="3" rx="0.5" fill="#fff" opacity="0.7"/>
      <rect x="11" y="2" width="3" height="3" rx="0.5" fill="#fff" opacity="0.7"/>
      <rect x="15" y="2" width="3" height="3" rx="0.5" fill="#fff" opacity="0.7"/>
      <rect x="${w - 5}" y="2" width="3" height="${h - 4}" rx="0.5" fill="#b3e5ff" opacity="0.5"/>
    </svg>`;
  } else if (vClass === "truck") {
    svgBody = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="${w - 2}" height="${h - 2}" rx="2" ry="2" fill="${color}" stroke="#222" stroke-width="1"/>
      <rect x="${w - 7}" y="1" width="6" height="${h - 2}" rx="1" fill="#3b82f6" stroke="#222" stroke-width="0.5"/>
      <rect x="${w - 6}" y="2" width="4" height="3" rx="0.5" fill="#dbeafe" opacity="0.8"/>
    </svg>`;
  } else {
    svgBody = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="${w - 2}" height="${h - 2}" rx="3" ry="3" fill="${color}" stroke="#222" stroke-width="1"/>
      <rect x="${w - 6}" y="2" width="4" height="${h - 4}" rx="1" fill="#b3e5ff" opacity="0.6"/>
      <rect x="3" y="2" width="3" height="${h - 4}" rx="1" fill="#b3e5ff" opacity="0.4"/>
    </svg>`;
  }

  return L.divIcon({
    className: "",
    html: `<div style="transform:rotate(${rotation - 90}deg);transform-origin:center;width:${w}px;height:${h}px;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.4));">${svgBody}</div>`,
    iconSize: [w, h],
    iconAnchor: [w / 2, h / 2],
  });
}

/* ─── Camera Center Icon (small pulsing dot) ─── */
const cameraMarkerIcon = L.divIcon({
  className: "",
  html: `<div style="width:12px;height:12px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 8px rgba(59,130,246,0.6);"></div>`,
  iconSize: [12, 12],
  iconAnchor: [6, 6],
});

/* ─── Map Setup ─── */
function MapSetup() {
  const map = useMap();
  useEffect(() => {
    setTimeout(() => map.invalidateSize(), 100);
    map.scrollWheelZoom.disable();
  }, [map]);
  return null;
}

/* ─── Main Component ─── */
export default function CctvMiniMap({
  cameraId,
  cameraName,
  lat,
  lng,
  pollInterval = 2000,
  detectMode = false,
  zoom = 20,
  interactive = false,
  showHud = true,
}: CctvMiniMapProps) {
  const [vehicles, setVehicles] = useState<CameraVehicle[]>([]);
  const [counts, setCounts] = useState({ car: 0, motorcycle: 0, bus: 0, truck: 0, total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const mountedRef = useRef(true);
  const center: [number, number] = useMemo(() => [lat, lng], [lat, lng]);

  const fetchVehicles = useCallback(async () => {
    try {
      const data = await getCameraVehicles(cameraId, 32);
      if (!mountedRef.current) return;
      setVehicles((data.vehicles || []).flatMap((v: Record<string, unknown>) => {
        const latValue = Number(v.lat);
        const lngValue = Number(v.lng);
        if (!Number.isFinite(latValue) || !Number.isFinite(lngValue)) {
          return [];
        }
        return [{
          id: String(v.id || ""),
          class: String(v.class || "car"),
          speed: Number(v.speed || 0),
          lat: latValue,
          lng: lngValue,
          angle: Number(v.angle || 0),
          distance: Number(v.distance || 0),
        }];
      }));
      setCounts(data.counts || { car: 0, motorcycle: 0, bus: 0, truck: 0, total: 0 });
      setLoading(false);
      setError(false);
    } catch {
      if (mountedRef.current) { setError(true); setLoading(false); }
    }
  }, [cameraId]);

  useEffect(() => {
    mountedRef.current = true;
    const initialTimer = window.setTimeout(() => {
      void fetchVehicles();
    }, 0);
    const interval = setInterval(fetchVehicles, pollInterval);
    return () => {
      mountedRef.current = false;
      window.clearTimeout(initialTimer);
      clearInterval(interval);
    };
  }, [fetchVehicles, pollInterval]);

  return (
    <div className="relative w-full h-full bg-gray-900">
      <MapContainer
        center={center}
        zoom={zoom}
        className="w-full h-full"
        zoomControl={false}
        attributionControl={false}
        dragging={interactive}
        doubleClickZoom={false}
        scrollWheelZoom={false}
        touchZoom={false}
        boxZoom={false}
        keyboard={false}
      >
        <TileLayer url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png" attribution="" maxZoom={22} />
        <MapSetup />
        <Circle
          center={center}
          radius={32}
          pathOptions={{ color: "#06b6d4", weight: 1.5, opacity: 0.9, fillColor: "#22d3ee", fillOpacity: 0.08 }}
        />
        <Marker position={center} icon={cameraMarkerIcon}>
          <Popup><span className="text-xs font-semibold">{cameraName}</span></Popup>
        </Marker>
        {vehicles.map((v) => (
          <Marker key={v.id} position={[v.lat, v.lng]} icon={makeVehicleIcon(v.class, v.angle)}>
            <Popup><span className="text-xs text-gray-700 capitalize">{v.class} — {v.speed.toFixed(1)} km/h</span></Popup>
          </Marker>
        ))}
      </MapContainer>

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900/60 z-[400]">
          <div className="text-center">
            <div className="animate-spin h-6 w-6 border-2 border-cyan-400 border-t-transparent rounded-full mx-auto mb-2" />
            <div className="text-xs text-gray-300">กำลังโหลด...</div>
          </div>
        </div>
      )}

      {error && !loading && vehicles.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900/40 z-[400]">
          <div className="text-xs text-gray-400">รอข้อมูลจากระบบ...</div>
        </div>
      )}

      {showHud && (
        <>
          <div className="absolute left-2 top-2 rounded-lg bg-white/90 px-2.5 py-1.5 text-[10px] text-slate-700 shadow-sm z-[500]">
            <div className="font-semibold text-slate-900">ตำแหน่งกล้องจริง</div>
            <div>{lat.toFixed(6)}, {lng.toFixed(6)}</div>
          </div>

          <div className="absolute bottom-2 left-2 bg-black/70 rounded-lg px-2.5 py-1.5 text-white text-[10px] space-y-0.5 z-[500]">
            <div className="font-semibold text-xs text-cyan-300">รถทั้งหมด: {counts.total}</div>
            <div className="flex gap-2">
              <span>🚗 {counts.car}</span>
              <span>🏍️ {counts.motorcycle}</span>
              <span>🚌 {counts.bus}</span>
              <span>🚛 {counts.truck}</span>
            </div>
          </div>

          {detectMode && (
            <span className="absolute top-2 right-2 text-[10px] px-1.5 py-0.5 rounded bg-green-500/90 text-white font-medium z-[500]">
              YOLO Detection
            </span>
          )}
        </>
      )}
    </div>
  );
}
