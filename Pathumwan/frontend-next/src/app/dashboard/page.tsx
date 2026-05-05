"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import dynamic from "next/dynamic";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import { getTrafficIndex } from "@/lib/api";
import type { TrafficIndexData } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex-1 flex items-center justify-center bg-gray-100">
      <div className="animate-spin h-8 w-8 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
    </div>
  ),
});

// Local places/roads for autocomplete
const LOCAL_PLACES = [
  { name: "ถนนพระรามที่ 1", lat: 13.7454, lng: 100.5344 },
  { name: "ถนนพระรามที่ 4", lat: 13.7328, lng: 100.5285 },
  { name: "ถนนพญาไท", lat: 13.7470, lng: 100.5290 },
  { name: "ถนนราชดำริ", lat: 13.7461, lng: 100.5399 },
  { name: "ถนนเพลินจิต", lat: 13.7445, lng: 100.5430 },
  { name: "ถนนบรรทัดทอง", lat: 13.7395, lng: 100.5265 },
  { name: "ถนนจารุเมือง", lat: 13.7380, lng: 100.5170 },
  { name: "ถนนวิทยุ", lat: 13.7439, lng: 100.5470 },
  { name: "ถนนอังรีดูนังต์", lat: 13.7310, lng: 100.5319 },
  { name: "ถนนสารสิน", lat: 13.7420, lng: 100.5350 },
  { name: "แยกปทุมวัน", lat: 13.7466, lng: 100.5291 },
  { name: "แยกสยาม", lat: 13.7454, lng: 100.5344 },
  { name: "แยกราชประสงค์", lat: 13.7461, lng: 100.5399 },
  { name: "แยกชิดลม", lat: 13.7445, lng: 100.5430 },
  { name: "แยกสามย่าน", lat: 13.7332, lng: 100.5291 },
  { name: "แยกอังรีดูนังต์", lat: 13.7310, lng: 100.5319 },
  { name: "แยกราชดำริ-สีลม", lat: 13.7320, lng: 100.5365 },
  { name: "แยกบรรทัดทอง", lat: 13.7395, lng: 100.5265 },
  { name: "แยกวิทยุ-เพลินจิต", lat: 13.7439, lng: 100.5470 },
  { name: "แยกหัวลำโพง", lat: 13.7380, lng: 100.5170 },
  { name: "สยามพารากอน", lat: 13.7463, lng: 100.5347 },
  { name: "เซ็นทรัลเวิลด์", lat: 13.7466, lng: 100.5391 },
  { name: "MBK Center", lat: 13.7446, lng: 100.5298 },
  { name: "สยามสแควร์", lat: 13.7450, lng: 100.5340 },
  { name: "BTS สยาม", lat: 13.7455, lng: 100.5340 },
  { name: "BTS ชิดลม", lat: 13.7441, lng: 100.5443 },
  { name: "MRT สามย่าน", lat: 13.7328, lng: 100.5285 },
  { name: "จุฬาลงกรณ์มหาวิทยาลัย", lat: 13.7382, lng: 100.5326 },
  { name: "สวนลุมพินี", lat: 13.7310, lng: 100.5415 },
  { name: "โรงพยาบาลจุฬาลงกรณ์", lat: 13.7320, lng: 100.5335 },
];

function indexColor(level: string) {
  switch (level) {
    case "คล่องตัว":
      return "bg-green-500";
    case "หนาแน่น":
      return "bg-yellow-500";
    case "ติดขัด":
      return "bg-orange-500";
    case "ติดขัดมาก":
      return "bg-red-600";
    default:
      return "bg-gray-400";
  }
}

function trafficSourceLabel(source?: string) {
  if (source === "live-state") return "Live state";
  if (source === "sumo-live") return "SUMO live";
  if (source === "sumo-camera") return "SUMO camera";
  if (source === "sumo-camera-fallback") return "SUMO camera fallback";
  if (source === "camera-detection") return "YOLO detection";
  if (source === "detection-fallback") return "YOLO fallback";
  if (source === "db-fallback") return "DB fallback";
  return source || "unknown";
}



export default function DashboardPage() {
  const [search, setSearch] = useState("");
  const [searchCoords, setSearchCoords] = useState<[number, number] | null>(null);
  const [indexData, setIndexData] = useState<TrafficIndexData | null>(null);
  const [suggestions, setSuggestions] = useState<typeof LOCAL_PLACES>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    async function fetchIndex() {
      try {
        const data = await getTrafficIndex();
        setIndexData(data);
      } catch {
        /* offline */
      }
    }
    fetchIndex();
    const interval = setInterval(fetchIndex, 5000);
    return () => clearInterval(interval);
  }, []);

  // Close suggestions when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    setSearch(value);
    if (value.trim().length > 0) {
      const q = value.trim().toLowerCase();
      const matches = LOCAL_PLACES.filter((p) => p.name.toLowerCase().includes(q));
      setSuggestions(matches.slice(0, 8));
      setShowSuggestions(matches.length > 0);
    } else {
      setSuggestions([]);
      setShowSuggestions(false);
    }
  }, []);

  function selectSuggestion(place: (typeof LOCAL_PLACES)[0]) {
    setSearch(place.name);
    setSearchCoords([place.lat, place.lng]);
    setShowSuggestions(false);
  }

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    setShowSuggestions(false);
    if (!search.trim()) return;

    // First check local places
    const local = LOCAL_PLACES.find((p) => p.name === search.trim());
    if (local) {
      setSearchCoords([local.lat, local.lng]);
      return;
    }

    try {
      const q = encodeURIComponent(search.trim() + " ปทุมวัน กรุงเทพ");
      const res = await fetch(
        `https://nominatim.openstreetmap.org/search?q=${q}&format=json&limit=1&countrycodes=th`
      );
      const results = await res.json();
      if (results.length > 0) {
        setSearchCoords([parseFloat(results[0].lat), parseFloat(results[0].lon)]);
      }
    } catch {
      /* offline */
    }
  }

  return (
    <ProtectedRoute>
      <div className="h-screen flex flex-col">
        <Navbar />
        <main className="flex-1 relative pt-14">
          {/* Map */}
          <MapView searchCoords={searchCoords} cameraPopup="stream" />

          {/* Search bar — top left overlay */}
          <form onSubmit={handleSearch} className="absolute top-[72px] left-4 z-[999]">
            <div ref={searchRef} className="relative">
              <div className="flex items-center bg-white/95 backdrop-blur shadow-lg rounded-lg border border-gray-200 overflow-hidden">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  onFocus={() => { if (suggestions.length > 0) setShowSuggestions(true); }}
                  placeholder="ค้นหาสถานที่หรือถนน"
                  className="px-4 py-2.5 w-64 text-sm text-gray-700 placeholder-gray-400 focus:outline-none"
                />
                <button
                  type="submit"
                  className="px-3 py-2.5 text-gray-400 hover:text-[#5ba8e0] transition-colors"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="11" cy="11" r="8" />
                    <line x1="21" y1="21" x2="16.65" y2="16.65" />
                  </svg>
                </button>
              </div>
              {/* Autocomplete dropdown */}
              {showSuggestions && suggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-white rounded-lg shadow-xl border border-gray-200 overflow-hidden max-h-60 overflow-y-auto">
                  {suggestions.map((place, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => selectSuggestion(place)}
                      className="w-full text-left px-4 py-2.5 text-sm text-gray-700 hover:bg-blue-50 hover:text-[#1e3a5f] transition-colors border-b border-gray-50 last:border-0 flex items-center gap-2"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-gray-400 shrink-0">
                        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                        <circle cx="12" cy="10" r="3" />
                      </svg>
                      {place.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </form>

          {/* Traffic index badge — top right overlay.
              Shows "ไม่มีข้อมูล" when the backend has no live data yet
              instead of a misleading 0.0, so admins can tell whether the
              system is genuinely calm vs not yet reporting. */}
          <div className="absolute top-[72px] right-4 z-[999]">
            {!indexData ? (
              <div className="min-w-[140px] rounded-xl bg-slate-700 px-5 py-3 text-center text-white shadow-lg">
                <div className="text-base font-bold leading-tight">กำลังโหลด</div>
                <div className="mt-1 text-[10px] opacity-90">ดัชนีรถติดแบบเรียลไทม์</div>
              </div>
            ) : indexData.data_available === false ? (
              <div className="bg-amber-500 text-white rounded-xl px-5 py-3 shadow-lg text-center min-w-[140px]">
                <div className="text-base font-bold leading-tight">
                  ไม่มีข้อมูล
                </div>
                <div className="text-[10px] mt-1 opacity-90">
                  รอระบบรายงานข้อมูลสด
                </div>
              </div>
            ) : (
              <div
                className={`${indexColor(indexData.level)} text-white rounded-xl px-5 py-3 shadow-lg text-center min-w-[140px]`}
              >
                <div className="text-3xl font-bold leading-none">
                  {indexData.index.toFixed(1)}
                </div>
                <div className="text-xs mt-1 opacity-90">ดัชนีรถติด</div>
                <div className="text-[10px] mt-0.5 opacity-75">
                  {indexData.level}
                </div>
                <div className="mt-1 text-[10px] opacity-80">
                  {trafficSourceLabel(indexData.source)}
                </div>
              </div>
            )}
          </div>

          {/* Bottom legend bar */}
        </main>
      </div>
    </ProtectedRoute>
  );
}
