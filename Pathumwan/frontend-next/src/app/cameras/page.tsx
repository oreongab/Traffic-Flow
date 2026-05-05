"use client";

import { useEffect, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import { getCameras } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { cameraPrimaryLabel, cameraSecondaryLabel } from "@/lib/cameraLabels";

const CctvFeed = dynamic(() => import("@/components/CctvFeed"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center bg-gray-900 rounded-lg aspect-video">
      <div className="animate-spin h-6 w-6 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
    </div>
  ),
});

type GridSize = 1 | 2 | 4 | 6 | 9;

function useRealtimeClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchName, setSearchName] = useState("");
  const [detectMode, setDetectMode] = useState(true);
  const [grid, setGrid] = useState<GridSize>(4);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const clock = useRealtimeClock();

  useEffect(() => {
    getCameras()
      .then((data) => {
        setCameras(data);
        const ids = data.slice(0, 6).map((c: Camera) => c.camera_id).filter(Boolean);
        setSelected(new Set(ids));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = cameras.filter((c) => {
    const haystack = `${cameraPrimaryLabel(c)} ${cameraSecondaryLabel(c)} ${c.camera_id}`.toLowerCase();
    if (searchName && !haystack.includes(searchName.toLowerCase()))
      return false;
    return true;
  });

  const toggleCamera = useCallback((cameraId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(cameraId)) next.delete(cameraId);
      else next.add(cameraId);
      return next;
    });
  }, []);

  const gridCols: Record<GridSize, string> = {
    1: "grid-cols-1",
    2: "grid-cols-1 md:grid-cols-2",
    4: "grid-cols-2",
    6: "grid-cols-2 lg:grid-cols-3",
    9: "grid-cols-3",
  };

  const selectedCameras = cameras.filter((c) => selected.has(c.camera_id));
  const displayCameras = selectedCameras.slice(0, grid);

  return (
    <ProtectedRoute adminOnly>
      <div className="h-screen flex flex-col">
        <Navbar />
        <div className="flex flex-1 pt-14 overflow-hidden">
          {/* Left panel */}
          <div className="w-80 bg-white border-r border-gray-200 flex flex-col overflow-hidden">
            {/* Search */}
            <div className="px-4 py-3 space-y-2 border-b border-gray-200">
              <div>
                <label className="text-xs text-gray-500 font-medium">
                  ค้นหากล้อง
                </label>
                <input
                  type="text"
                  value={searchName}
                  onChange={(e) => setSearchName(e.target.value)}
                  placeholder="ค้นหาตามชื่อถนน / แยก"
                  className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 placeholder-gray-400 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                />
              </div>
            </div>

            {/* Camera list */}
            <div className="px-4 py-2 border-b border-gray-200">
              <span className="text-xs text-gray-500 font-medium">
                รายการกล้อง ({filtered.length})
              </span>
            </div>
            <div className="flex-1 overflow-y-auto">
              {loading ? (
                <div className="flex justify-center py-10">
                  <div className="animate-spin h-6 w-6 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
                </div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {filtered.map((c) => (
                    <button
                      key={c.camera_id}
                      onClick={() => toggleCamera(c.camera_id)}
                      className={`w-full text-left px-4 py-2.5 hover:bg-blue-50 transition-colors flex items-center gap-3 ${
                        selected.has(c.camera_id) ? "bg-blue-50" : ""
                      }`}
                    >
                      <div
                        className={`w-3 h-3 rounded-full border-2 flex-shrink-0 ${selected.has(c.camera_id)
                            ? "bg-[#5ba8e0] border-[#5ba8e0]"
                            : "border-gray-300"
                        }`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium text-[#1e3a5f] truncate" title={cameraPrimaryLabel(c)}>
                          {cameraPrimaryLabel(c)}
                        </div>
                        <div className="mt-0.5 text-[11px] text-gray-400 truncate" title={cameraSecondaryLabel(c)}>
                          {cameraSecondaryLabel(c) || "ตำแหน่งกล้องจราจร"}
                        </div>
                        <div className="mt-1">
                          <span
                            className="inline-block max-w-full truncate rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500 align-top"
                            title={c.camera_id}
                          >
                            ID {c.camera_id}
                          </span>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right — feeds */}
          <div className="flex-1 flex flex-col bg-gray-100 overflow-hidden">
            {/* Grid selector + YOLO toggle */}
            <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-gray-200">
              <span className="text-xs text-gray-500 mr-2">แสดง:</span>
              {([1, 2, 4, 6, 9] as GridSize[]).map((n) => (
                <button
                  key={n}
                  onClick={() => setGrid(n)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                    grid === n
                      ? "bg-[#5ba8e0] text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                  }`}
                >
                  {n}
                </button>
              ))}
              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={() => setDetectMode(!detectMode)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
                    detectMode
                      ? "bg-green-500 text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                  }`}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" />
                    <path d="M3 9h18M9 3v18" />
                  </svg>
                  YOLO Detection
                </button>
              </div>
            </div>

            {/* Feeds grid */}
            <div className={`flex-1 p-3 overflow-auto ${grid === 1 ? "flex flex-col" : `grid ${gridCols[grid]} gap-3`}`}>
              {displayCameras.map((cam) => {
                const timeStr = clock.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
                const dateStr = clock.toLocaleDateString("th-TH", { year: "numeric", month: "short", day: "numeric" });
                const isSingle = grid === 1;
                return (
                  <div
                    key={cam.camera_id}
                    className={`bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col ${isSingle ? "flex-1" : ""}`}
                  >
                    <div className="px-3 py-2 border-b border-gray-100 flex items-center justify-between flex-shrink-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse flex-shrink-0" />
                        <span className="text-sm font-medium text-[#1e3a5f] truncate">
                          {cameraPrimaryLabel(cam)}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {detectMode && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700 font-medium">
                            YOLO
                          </span>
                        )}
                        <span className="text-[10px] text-gray-400 truncate max-w-48">
                          {cameraSecondaryLabel(cam) || "ตำแหน่งกล้องจราจร"}
                        </span>
                      </div>
                    </div>
                    <div className={`${isSingle ? "flex-1" : "aspect-video"} bg-gray-900 relative`}>
                      <CctvFeed
                        cameraId={cam.camera_id}
                        cameraName={cameraPrimaryLabel(cam)}
                        subtitle={cameraSecondaryLabel(cam)}
                        detectMode={detectMode}
                        cameraLat={cam.lat}
                        cameraLng={cam.lng}
                      />
                      {/* Timestamp overlay — top right */}
                      <div className="absolute top-2 right-2 bg-black/60 rounded px-2 py-1 text-white text-[10px] font-mono z-[500]">
                        <div>{dateStr}</div>
                        <div className="text-cyan-300 font-semibold">{timeStr}</div>
                      </div>
                      {/* REC indicator — top left */}
                      <div className="absolute top-2 left-2 flex items-center gap-1 z-[500]">
                        <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                        <span className="text-[9px] font-semibold text-red-400">REC</span>
                      </div>
                    </div>
                  </div>
                );
              })}

              {displayCameras.length === 0 && (
                <div className="col-span-full flex items-center justify-center text-gray-400 text-sm py-20">
                  เลือกกล้องจากรายการด้านซ้าย
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
