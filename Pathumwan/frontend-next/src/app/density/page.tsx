"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import { getDensity, getCameras, getRoadGeometries } from "@/lib/api";
import type { RoadDensity, Camera, RoadGeometry } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex-1 flex items-center justify-center bg-gray-100">
      <div className="animate-spin h-8 w-8 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
    </div>
  ),
});

function badgeStyle(level: string) {
  switch (level) {
    case "คล่องตัว": return "bg-green-500 text-white";
    case "หนาแน่น": return "bg-yellow-500 text-white";
    case "ติดขัด": return "bg-orange-500 text-white";
    case "ติดมาก": return "bg-red-600 text-white";
    case "ไม่มีข้อมูล": return "bg-gray-300 text-gray-700";
    default: return "bg-gray-400 text-white";
  }
}

function indexBarColor(index: number, hasData: boolean): string {
  if (!hasData) return "bg-gray-300";
  if (index <= 3) return "bg-green-500";
  if (index <= 5) return "bg-yellow-500";
  if (index <= 7) return "bg-orange-500";
  return "bg-red-600";
}

function sourceLabel(source?: string) {
  if (source === "live-state") return "YOLO + runtime state";
  if (source === "real") return "YOLO detection";
  if (source === "sumo-live") return "จำลองเสมือนจริง + วิเคราะห์สด";
  if (source === "detection-fallback") return "YOLO fallback";
  if (source === "db-cache") return "ฐานข้อมูลสำรอง";
  return source || "runtime analytics";
}


export default function DensityPage() {
  const [roads, setRoads] = useState<RoadDensity[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [geoms, setGeoms] = useState<RoadGeometry[]>([]);
  const [loading, setLoading] = useState(true);
  const [focusBounds, setFocusBounds] = useState<[[number, number], [number, number]] | null>(null);
  const [activeRoadId, setActiveRoadId] = useState<string | null>(null);

  const geomMap = useMemo(() => new Map(geoms.map((g) => [g.road_id, g])), [geoms]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const g = await getRoadGeometries();
        if (active) setGeoms(g);
      } catch {
        if (active) setGeoms([]);
      }
    })();
    return () => { active = false; };
  }, []);

  // Cameras only need to load once (used as fallback bbox source).
  useEffect(() => {
    let active = true;
    getCameras()
      .then((list) => { if (active) setCameras(list); })
      .catch(() => { /* offline */ });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    async function fetchDensity() {
      try {
        const density = await getDensity();
        if (active) setRoads(density);
      } catch { /* offline */ }
      finally { if (active) setLoading(false); }
    }
    fetchDensity();
    const interval = setInterval(fetchDensity, 5000);
    return () => { active = false; clearInterval(interval); };
  }, []);

  return (
    <ProtectedRoute>
      <div className="h-screen flex flex-col">
        <Navbar />
        <div className="flex flex-1 pt-14 overflow-hidden">
          <div className="w-96 bg-white border-r border-gray-200 flex flex-col overflow-hidden z-10">
            <div className="px-4 py-3 border-b border-gray-200">
                <h2 className="text-lg font-bold text-[#1e3a5f]">ระดับความหนาแน่น</h2>
                <p className="text-xs text-gray-400 mt-0.5">ความหนาแน่นและความเร็วคำนวณจาก YOLO detection และ runtime analytics ที่อัปเดตต่อเนื่อง</p>
              </div>
            <div className="flex-1 overflow-y-auto">
              {loading ? (
                <div className="flex justify-center py-20">
                  <div className="animate-spin h-8 w-8 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
                </div>
              ) : roads.length === 0 ? (
                <p className="text-gray-400 text-center py-20">ไม่มีข้อมูล</p>
              ) : (
                <div className="divide-y divide-gray-100">
                  {roads.map((r, i) => (
                    <button
                      key={r.road_id || r.road || String(i)}
                      type="button"
                      onClick={() => {
                        const roadId = r.road_id || "";
                        setActiveRoadId(roadId);
                        const geom = roadId ? geomMap.get(roadId) : undefined;
                        if (geom?.bbox) {
                          setFocusBounds([
                            [geom.bbox.min_lat, geom.bbox.min_lng],
                            [geom.bbox.max_lat, geom.bbox.max_lng],
                          ]);
                          return;
                        }

                        // Geometry hasn't loaded yet — fit bounds around ALL cameras on this
                        // road (strict road_id match only) so we don't jump to a random camera
                        // whose name happens to contain a matching substring.
                        if (!roadId) return;
                        const roadCams = cameras.filter((camera) => camera.road_id === roadId);
                        if (roadCams.length === 0) return;
                        const lats = roadCams.map((c) => c.lat);
                        const lngs = roadCams.map((c) => c.lng);
                        setFocusBounds([
                          [Math.min(...lats), Math.min(...lngs)],
                          [Math.max(...lats), Math.max(...lngs)],
                        ]);
                      }}
                      className="w-full px-4 py-3 text-left hover:bg-gray-50 transition-colors"
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <h3 className="font-semibold text-[#1e3a5f] text-sm truncate">{r.road}</h3>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-400">{r.source_label || sourceLabel(r.source)}</span>
                          {r.is_fallback && (
                            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">fallback</span>
                          )}
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${badgeStyle(r.level)}`}>
                            {r.level}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${indexBarColor(r.index, r.has_data !== false)}`}
                            style={{ width: `${r.has_data === false ? 100 : Math.min(100, (r.index / 10) * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs font-bold text-[#1e3a5f] min-w-[28px] text-right">
                          {r.has_data === false ? "—" : r.index.toFixed(1)}
                        </span>
                      </div>
                      <div className="flex gap-4 text-xs text-gray-500">
                        <span>จำนวนรถ {r.has_data === false ? "—" : `${r.vehicle_count} คัน`}</span>
                        <span>ความเร็ว {r.has_data !== false && r.speed > 0 ? `${r.speed.toFixed(0)} km/h` : "ไม่มีข้อมูล"}</span>
                      </div>
                      {r.metric_source && (
                        <div className="mt-1 text-[10px] text-gray-400">
                          metric: {r.metric_source}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <main className="flex-1 relative">
            <MapView
              showVehicles
              showCameras={false}
              showLights={false}
              fitBounds={focusBounds}
              activeRoadId={activeRoadId}
            />
          </main>
        </div>
      </div>
    </ProtectedRoute>
  );
}
