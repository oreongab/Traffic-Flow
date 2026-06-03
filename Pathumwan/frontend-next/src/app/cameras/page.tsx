"use client";

import { useEffect, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import { flushSync } from "react-dom";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import { getCameras } from "@/lib/api";
import type { Camera } from "@/lib/types";
import { cameraPrimaryLabel, cameraSecondaryLabel } from "@/lib/cameraLabels";

const MAX_VISIBLE_CAMERAS = 6;

const CctvFeed = dynamic(() => import("@/components/CctvFeed"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center bg-gray-900 rounded-lg aspect-video">
      <div className="animate-spin h-6 w-6 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
    </div>
  ),
});

type GridSize = 1 | 2 | 4 | 6;


export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchName, setSearchName] = useState("");
  const [detectMode, setDetectMode] = useState(true);
  const [grid, setGrid] = useState<GridSize>(4);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [streamsPaused, setStreamsPaused] = useState(false);

  useEffect(() => {
    function stopStreamsForPageNavigation(event: Event) {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (anchor.origin !== window.location.origin) return;
      if (anchor.pathname === window.location.pathname) return;
      flushSync(() => setStreamsPaused(true));
      window.dispatchEvent(new Event("traffixflow:stop-cctv-streams"));
    }

    document.addEventListener("pointerdown", stopStreamsForPageNavigation, true);
    document.addEventListener("click", stopStreamsForPageNavigation, true);
    return () => {
      document.removeEventListener("pointerdown", stopStreamsForPageNavigation, true);
      document.removeEventListener("click", stopStreamsForPageNavigation, true);
    };
  }, []);

  useEffect(() => {
    let active = true;

    async function refreshCameras() {
      try {
        const data = await getCameras();
        if (!active) return;
        setCameras(data);
        setSelected((prev) => {
          if (prev.size > 0) {
            return new Set(Array.from(prev).slice(0, MAX_VISIBLE_CAMERAS));
          }
          const primary = data.filter((c: Camera) => c.research_target);
          const ids = (primary.length > 0 ? primary : data).slice(0, MAX_VISIBLE_CAMERAS).map((c: Camera) => c.camera_id).filter(Boolean);
          return new Set(ids);
        });
      } catch {
        /* offline */
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void refreshCameras();
    const interval = setInterval(() => void refreshCameras(), 10000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const researchCameras = cameras.filter((c) => c.research_target);
  const cameraRoster = researchCameras.length > 0 ? researchCameras : cameras;
  const filtered = cameraRoster.filter((c) => {
    const haystack = `${cameraPrimaryLabel(c)} ${cameraSecondaryLabel(c)} ${c.camera_id}`.toLowerCase();
    if (searchName && !haystack.includes(searchName.toLowerCase()))
      return false;
    return true;
  });

  const toggleCamera = useCallback((cameraId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(cameraId)) {
        next.delete(cameraId);
        return next;
      }
      if (next.size >= MAX_VISIBLE_CAMERAS) {
        return next;
      }
      next.add(cameraId);
      return next;
    });
  }, []);

  const selectGrid = useCallback((nextGrid: GridSize) => {
    setGrid(nextGrid);
    setSelected((prev) => {
      const kept = Array.from(prev).slice(0, nextGrid);
      if (kept.length >= nextGrid) return new Set(kept);
      const next = new Set(kept);
      for (const camera of filtered) {
        if (next.size >= nextGrid) break;
        if (camera.camera_id) next.add(camera.camera_id);
      }
      return next;
    });
  }, [filtered]);

  const gridCols: Record<GridSize, string> = {
    1: "grid-cols-1",
    2: "grid-cols-1 md:grid-cols-2",
    4: "grid-cols-1 sm:grid-cols-2",
    6: "grid-cols-1 sm:grid-cols-2 xl:grid-cols-3",
  };
  const gridRows: Record<GridSize, string> = {
    1: "",
    2: "lg:grid-rows-1",
    4: "lg:grid-rows-2",
    6: "lg:grid-rows-2",
  };

  const selectedCameras = cameraRoster.filter((c) => selected.has(c.camera_id));
  const displayCameras = streamsPaused
    ? []
    : selectedCameras.slice(0, Math.min(grid, MAX_VISIBLE_CAMERAS));
  const streamFps = grid >= 6 ? (detectMode ? 2 : 3) : detectMode ? 4 : 6;

  return (
    <ProtectedRoute>
      <div className="flex h-[100dvh] min-h-0 flex-col">
        <Navbar />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden pt-14 lg:flex-row">
          {/* Left panel */}
          <div className="flex max-h-[42dvh] w-full shrink-0 flex-col overflow-hidden border-b border-gray-200 bg-white lg:max-h-none lg:w-80 lg:border-b-0 lg:border-r">
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
                รายการ CCTV ชุดงานวิจัย ({filtered.length}) • เลือกได้สูงสุด {MAX_VISIBLE_CAMERAS}
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
                      className={`w-full text-left px-4 py-2.5 transition-colors flex items-center gap-3 ${
                        selected.has(c.camera_id)
                          ? "bg-blue-50"
                          : selected.size >= MAX_VISIBLE_CAMERAS
                            ? "opacity-55 cursor-not-allowed"
                            : "hover:bg-blue-50"
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
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-gray-100">
            {/* Grid selector + YOLO toggle */}
            <div className="flex flex-wrap items-center gap-2 border-b border-gray-200 bg-white px-3 py-2 sm:px-4">
              <span className="text-xs text-gray-500 mr-2">แสดง:</span>
              {([1, 2, 4, 6] as GridSize[]).map((n) => (
                <button
                  key={n}
                  onClick={() => selectGrid(n)}
                  className={`min-w-9 rounded px-3 py-1 text-xs font-medium transition-colors ${
                    grid === n
                      ? "bg-[#5ba8e0] text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                  }`}
                >
                  {n}
                </button>
              ))}
              <div className="ml-0 flex items-center gap-2 sm:ml-auto">
                <button
                  onClick={() => setDetectMode(!detectMode)}
                  className={`flex items-center gap-1.5 rounded px-3 py-1 text-xs font-medium transition-colors ${
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
            <div className={`min-h-0 flex-1 p-2 sm:p-3 ${grid === 1 ? "flex flex-col overflow-hidden" : `grid ${gridCols[grid]} ${gridRows[grid]} auto-rows-max gap-2 overflow-auto sm:gap-3 lg:auto-rows-fr`}`}>
              {displayCameras.map((cam) => {
                const isSingle = grid === 1;
                return (
                  <div
                    key={cam.camera_id}
                    className={`flex min-h-0 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm ${isSingle ? "flex-1" : ""}`}
                  >
                    <div className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-100 px-3 py-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse flex-shrink-0" />
                        <span className="text-sm font-medium text-[#1e3a5f] truncate">
                          {cameraPrimaryLabel(cam)}
                        </span>
                      </div>
                      <div className="flex min-w-0 shrink items-center justify-end gap-2">
                        {detectMode && (
                          <span className="shrink-0 rounded bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700">
                            YOLO
                          </span>
                        )}
                        <span className="hidden max-w-[12rem] truncate text-[10px] text-gray-400 sm:block">
                          {cameraSecondaryLabel(cam) || "ตำแหน่งกล้องจราจร"}
                        </span>
                      </div>
                    </div>
                    <div className={`${isSingle ? "min-h-[360px] flex-1" : "aspect-video lg:aspect-auto lg:min-h-0 lg:flex-1"} relative bg-gray-900`}>
                      <CctvFeed
                        cameraId={cam.camera_id}
                        cameraName={cameraPrimaryLabel(cam)}
                        subtitle={cameraSecondaryLabel(cam)}
                        detectMode={detectMode}
                        cameraLat={cam.lat}
                        cameraLng={cam.lng}
                        showCounts={false}
                        showMiniMap={false}
                        streamFps={streamFps}
                        streamMode={detectMode ? "detect" : "raw"}
                        streamTransport="snapshot"
                        showInfoOverlay={false}
                        eagerStream={true}
                        showPoster={false}
                      />
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
