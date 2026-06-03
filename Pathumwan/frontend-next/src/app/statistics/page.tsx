"use client";

import { useEffect, useState } from "react";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import {
  getIndexToday,
  getIndexWeekly,
  getTopRoads,
  getDailyCount,
  getYearlyStats,
  getAvailableYears,
  getHourlyVehicleCounts,
  getTrafficIndex,
  getDensity,
} from "@/lib/api";
import type {
  HourlyIndex,
  WeeklyIndex,
  TopRoad,
  DailyCount,
  YearlyStat,
  HourlyVehicleCount,
  TrafficIndexData,
  RoadDensity,
} from "@/lib/types";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import { Line, Bar } from "react-chartjs-2";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Tooltip,
  Legend,
  Filler
);

type Tab = "realtime" | "index" | "yearly" | "daily" | "top10";

const tabs: { key: Tab; label: string }[] = [
  { key: "realtime", label: "ข้อมูลเรียลไทม์" },
  { key: "index", label: "ดัชนีรถติด" },
  { key: "yearly", label: "สถิติประจำปี" },
  { key: "daily", label: "จำนวนรถในแต่ละวัน" },
  { key: "top10", label: "TOP 10 ถนนที่รถติดมากที่สุด" },
];

function indexColor(val: number) {
  if (val <= 3) return "bg-green-100 text-green-700";
  if (val <= 5) return "bg-yellow-100 text-yellow-700";
  if (val <= 7) return "bg-orange-100 text-orange-700";
  return "bg-red-100 text-red-700";
}

function toIsoDate(value?: string) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) {
    return new Date().toISOString().slice(0, 10);
  }
  return date.toISOString().slice(0, 10);
}

function liveTimeLabel(value?: string) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
}

export default function StatisticsPage() {
  const [tab, setTab] = useState<Tab>("realtime");
  const [year, setYear] = useState(new Date().getFullYear());
  const [years, setYears] = useState<number[]>([]);
  const [hourly, setHourly] = useState<HourlyIndex[]>([]);
  const [weekly, setWeekly] = useState<WeeklyIndex[]>([]);
  const [yearly, setYearly] = useState<YearlyStat[]>([]);
  const [topRoads, setTopRoads] = useState<TopRoad[]>([]);
  const [dailyCount, setDailyCount] = useState<DailyCount[]>([]);
  const [hourlyCounts, setHourlyCounts] = useState<HourlyVehicleCount[]>([]);
  const [realtimeData, setRealtimeData] = useState<Record<string, unknown>[]>([]);
  const [liveIndex, setLiveIndex] = useState<TrafficIndexData | null>(null);
  const [liveRoads, setLiveRoads] = useState<RoadDensity[]>([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(() => new Date());
  const currentYear = new Date().getFullYear();

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    getAvailableYears()
      .then((y) => setYears(y))
      .catch(() =>
        setYears(
          Array.from({ length: 5 }, (_, i) => new Date().getFullYear() - i)
        )
      );
  }, []);

  useEffect(() => {
    let active = true;

    async function loadHistorical(initial: boolean) {
      if (initial) setLoading(true);
      const [h, w, ys, t, d, hc] = await Promise.all([
        getIndexToday().catch(() => []),
        getIndexWeekly().catch(() => []),
        getYearlyStats(year).catch(() => []),
        getTopRoads(year).catch(() => []),
        getDailyCount(year).catch(() => []),
        getHourlyVehicleCounts().catch(() => []),
      ]);
      if (!active) return;
      setHourly(h as HourlyIndex[]);
      setWeekly(w as WeeklyIndex[]);
      setYearly(ys as YearlyStat[]);
      setTopRoads(t as TopRoad[]);
      setDailyCount(d as DailyCount[]);
      setHourlyCounts(hc as HourlyVehicleCount[]);
      if (initial) setLoading(false);
    }

    loadHistorical(true);
    // Re-poll every 30s so non-realtime tabs (ดัชนี / ปี / รายวัน / TOP10) stay live
    // without requiring a page refresh. Matches backend INDEX_INTERVAL.
    const interval = setInterval(() => loadHistorical(false), 30000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [year]);

  // Poll calculated realtime data every few seconds so all tabs for the
  // current year can reflect the live stream, not just the realtime tab.
  useEffect(() => {
    let active = true;
    const fetchRealtime = async () => {
      try {
        const [idx, density, hc] = await Promise.all([
          getTrafficIndex().catch(() => null),
          getDensity().catch(() => []),
          getHourlyVehicleCounts().catch(() => []),
        ]);
        if (!active) return;
        if (idx) setLiveIndex(idx);
        setLiveRoads(density as RoadDensity[]);
        setHourlyCounts(hc as HourlyVehicleCount[]);

        // Fetch YOLO realtime counts via api helper
        const { default: api } = await import("@/lib/api");
        const res = await api.get("/stats/realtime-counts");
        if (active) {
          setRealtimeData((res.data.data || []) as Record<string, unknown>[]);
        }
      } catch { /* offline */ }
    };
    fetchRealtime();
    const interval = setInterval(fetchRealtime, 5000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const liveTimestamp = now.toISOString();
  const liveDate = toIsoDate(liveTimestamp);
  const liveRealtimeTotal = realtimeData.reduce(
    (sum, row) => sum + Number(row.total || 0),
    0
  );
  const liveRoadTotal = liveRoads.reduce((sum, row) => sum + Number(row.vehicle_count || 0), 0);
  const liveDailyTotal = Math.max(liveRealtimeTotal, liveRoadTotal);
  const liveRoadRanks = liveRoads
    .filter((road) => road.has_data !== false && (road.index > 0 || road.vehicle_count > 0 || road.speed > 0))
    .sort((a, b) => b.index - a.index)
    .slice(0, 10)
    .map((road) => ({ road: road.road, avg_max_index: road.index }));
  const hasLiveIndex = year === currentYear && !!liveIndex && liveIndex.data_available !== false;

  const displayedHourly = [...hourly];
  if (hasLiveIndex) {
    const currentLabel = liveTimeLabel(liveTimestamp);
    const existingIdx = displayedHourly.findIndex((row) => row.time === currentLabel);
    const liveRow = {
      hour: Number(new Date(liveTimestamp).getHours() || 0),
      time: currentLabel,
      index: liveIndex.index,
    };
    if (existingIdx >= 0) {
      displayedHourly[existingIdx] = liveRow;
    } else if (currentLabel) {
      displayedHourly.push(liveRow);
    }
  }

  const displayedWeekly = [...weekly];
  if (hasLiveIndex) {
    const existingIdx = displayedWeekly.findIndex((row) => row.date === liveDate);
    const existing = existingIdx >= 0 ? displayedWeekly[existingIdx] : null;
    const nextRow = {
      date: liveDate,
      max_index: Math.max(existing?.max_index || 0, liveIndex.index),
      avg_index: existing ? Math.max(existing.avg_index, liveIndex.index) : liveIndex.index,
    };
    if (existingIdx >= 0) {
      displayedWeekly[existingIdx] = nextRow;
    } else {
      displayedWeekly.unshift(nextRow);
    }
  }

  const displayedYearly = [...yearly];
  if (hasLiveIndex) {
    const existingIdx = displayedYearly.findIndex((row) => row.date === liveDate);
    const existing = existingIdx >= 0 ? displayedYearly[existingIdx] : null;
    const nextRow = {
      date: liveDate,
      time: liveTimeLabel(liveTimestamp),
      max_index: Math.max(existing?.max_index || 0, liveIndex.index),
      peak_index: Math.max(existing?.peak_index || 0, liveIndex.index),
      peak_time: liveTimeLabel(liveTimestamp),
      avg_index: existing?.avg_index ?? liveIndex.index,
    };
    if (existingIdx >= 0) {
      displayedYearly[existingIdx] = nextRow;
    } else {
      displayedYearly.unshift(nextRow);
    }
  }

  const displayedDailyCount = [...dailyCount];
  if (year === currentYear && liveDailyTotal > 0) {
    const existingIdx = displayedDailyCount.findIndex((row) => row.date === liveDate);
    const nextRow = {
      date: liveDate,
      total_vehicles: Math.max(displayedDailyCount[existingIdx]?.total_vehicles || 0, liveDailyTotal),
    };
    if (existingIdx >= 0) {
      displayedDailyCount[existingIdx] = nextRow;
    } else {
      displayedDailyCount.unshift(nextRow);
    }
  }

  const displayedTopRoads = [...topRoads];
  if (year === currentYear && liveRoadRanks.length > 0) {
    for (const liveRoad of liveRoadRanks) {
      const existingIdx = displayedTopRoads.findIndex((row) => row.road === liveRoad.road);
      if (existingIdx >= 0) {
        displayedTopRoads[existingIdx] = {
          ...displayedTopRoads[existingIdx],
          avg_max_index: Math.max(displayedTopRoads[existingIdx].avg_max_index, liveRoad.avg_max_index),
        };
      } else {
        displayedTopRoads.push(liveRoad);
      }
    }
  }
  displayedTopRoads.sort((a, b) => b.avg_max_index - a.avg_max_index);

  const chartData = {
    labels: displayedHourly.map((h) => {
      const dt = new Date(h.time);
      return Number.isNaN(dt.getTime())
        ? `${String(h.hour).padStart(2, "0")}:00`
        : dt.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
    }),
    datasets: [
      {
        label: "ดัชนีจราจร",
        data: displayedHourly.map((h) => h.index),
        borderColor: "#5ba8e0",
        backgroundColor: "rgba(91,168,224,0.1)",
        fill: true,
        tension: 0.3,
        pointRadius: 3,
      },
    ],
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#374151" } },
    },
    scales: {
      x: { ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" } },
      y: {
        min: 0,
        max: 10,
        ticks: { color: "#6b7280" },
        grid: { color: "#e5e7eb" },
      },
    },
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <Navbar />
        <div className="pt-14">
          {/* Tabs */}
          <div className="bg-white border-b border-gray-200">
            <div className="flex overflow-x-auto px-4">
              {tabs.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`px-4 py-3 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                    tab === t.key
                      ? "border-[#5ba8e0] text-[#5ba8e0]"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {t.label}
                </button>
              ))}
              {/* Year selector */}
              <div className="ml-auto flex items-center">
                <select
                  value={year}
                  onChange={(e) => setYear(Number(e.target.value))}
                  className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                >
                  {(years.length > 0
                    ? years
                    : Array.from(
                        { length: 5 },
                        (_, i) => new Date().getFullYear() - i
                      )
                  ).map((y) => (
                    <option key={y} value={y}>
                      {y + 543}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="p-6">
            {loading ? (
              <div className="flex justify-center py-20">
                <div className="animate-spin h-8 w-8 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
              </div>
            ) : (
              <>
                {/* ========= ข้อมูลเรียลไทม์ ========= */}
                {tab === "realtime" && (
                  <div className="space-y-6">
                    {/* Live traffic index summary */}
                    {liveIndex && (
                      <div className="bg-white rounded-xl border border-gray-200 p-6">
                        <div className="flex items-center justify-between mb-4">
                          <div>
                            <h2 className="text-lg font-bold text-[#1e3a5f]">
                              สถานะจราจรแบบเรียลไทม์
                            </h2>
                            <p className="text-xs text-gray-500 mt-1">
                              ระบบนี้เน้นการวิเคราะห์แบบเรียลจาก YOLO detection แล้วคำนวณต่อเป็นความเร็ว ความหนาแน่น และดัชนีจราจร โดยโหมดจำลองจะใช้ runtime เสมือนจริงเป็นฐานข้อมูลภาพ
                            </p>
                          </div>
                          {liveIndex.data_available === false ? (
                            <span className="flex items-center gap-1.5 text-xs text-amber-600 bg-amber-50 px-2.5 py-1 rounded-full">
                              <span className="w-2 h-2 rounded-full bg-amber-400" />
                              ไม่มีข้อมูล
                            </span>
                          ) : (
                            <span className="flex items-center gap-1.5 text-xs text-green-600 bg-green-50 px-2.5 py-1 rounded-full">
                              <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                              LIVE
                            </span>
                          )}
                        </div>
                        {liveIndex.data_available === false ? (
                          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                            ยังไม่มีข้อมูลจราจร — ตรวจสอบว่า runtime กล้องและตัววิเคราะห์กำลังทำงานอยู่
                            (ค่ายังไม่อัปเดต ไม่ได้แปลว่าถนนว่าง)
                          </div>
                        ) : (
                          <div className="flex items-center gap-4">
                            <div
                              className="w-20 h-20 rounded-xl flex items-center justify-center text-white text-2xl font-bold"
                              style={{ backgroundColor: liveIndex.color }}
                            >
                              {liveIndex.index.toFixed(1)}
                            </div>
                            <div>
                              <div className="text-lg font-semibold text-[#1e3a5f]">{liveIndex.level}</div>
                              <div className="text-xs text-gray-400">อัปเดตล่าสุด: {now.toLocaleTimeString("th-TH")}</div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Live road density table */}
                    {liveRoads.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-6">
                          <h2 className="text-lg font-bold text-[#1e3a5f] mb-4">
                            ความหนาแน่นจราจรแต่ละถนน (YOLO analytics)
                          </h2>
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="bg-gray-50 text-gray-600">
                                <th className="text-left py-2 px-3">ถนน</th>
                                <th className="text-right py-2 px-3">จำนวนรถ</th>
                                <th className="text-right py-2 px-3">ความเร็ว</th>
                                <th className="text-right py-2 px-3">ดัชนี</th>
                                <th className="text-center py-2 px-3">ระดับ</th>
                              </tr>
                            </thead>
                            <tbody>
                              {liveRoads.map((rd, i) => (
                                <tr key={i} className="border-t border-gray-100 hover:bg-gray-50">
                                  <td className="py-2 px-3 text-gray-700 font-medium">{rd.road}</td>
                                  <td className="py-2 px-3 text-right font-mono">{rd.vehicle_count}</td>
                                  <td className="py-2 px-3 text-right font-mono">{rd.speed.toFixed(1)} km/h</td>
                                  <td className="py-2 px-3 text-right">
                                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${indexColor(rd.index)}`}>
                                      {rd.index.toFixed(1)}
                                    </span>
                                  </td>
                                  <td className="py-2 px-3 text-center text-xs text-gray-500">{rd.level}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {/* YOLO detection vehicle counts by road */}
                    <div className="bg-white rounded-xl border border-gray-200 p-6">
                      <div className="flex items-center justify-between mb-4">
                        <div>
                          <h2 className="text-lg font-bold text-[#1e3a5f]">
                            จำนวนรถตามถนน (ตรวจจับจากกล้อง)
                          </h2>
                          <p className="text-xs text-gray-500 mt-1">
                            รวมจาก YOLO ต่อถนนแบบ conservative เพื่อเลี่ยงการนับซ้ำระหว่างหลายกล้องบนถนนเดียวกัน และค่อย fallback เป็น runtime จำลองเมื่อยังไม่มี detection
                          </p>
                        </div>
                      </div>

                      {realtimeData.length === 0 ? (
                        <p className="text-gray-400 text-sm py-8 text-center">
                          รอข้อมูลจากระบบตรวจจับ...
                        </p>
                      ) : (
                        <div className="space-y-3">
                          {realtimeData.map((rd, i) => (
                            <div
                              key={String(rd.road_id)}
                              className="flex items-center gap-4 p-3 bg-gray-50 rounded-lg border border-gray-100"
                            >
                              <div className="w-8 h-8 rounded-full bg-[#5ba8e0] text-white flex items-center justify-center text-xs font-bold flex-shrink-0">
                                {i + 1}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium text-[#1e3a5f]">
                                  {String(rd.road_name)}
                                </div>
                                <div className="text-[10px] text-gray-400">
                                  {String(rd.source || "").startsWith("sumo") && (
                                    <span className="text-blue-500">Runtime จำลอง</span>
                                  )}
                                  {rd.source === "camera-detection" && (
                                    <span className="text-amber-500">YOLO Detection</span>
                                  )}
                                  {rd.avg_speed ? ` • ${Number(rd.avg_speed).toFixed(1)} km/h` : ""}
                                </div>
                              </div>
                              <div className="flex gap-3 text-xs text-gray-600">
                                <span title="รถยนต์">🚗 {Number(rd.car || 0)}</span>
                                <span title="มอเตอร์ไซค์">🏍️ {Number(rd.motorcycle || 0)}</span>
                                <span title="รถบัส">🚌 {Number(rd.bus || 0)}</span>
                                <span title="รถบรรทุก">🚛 {Number(rd.truck || 0)}</span>
                              </div>
                              <div className="text-right flex-shrink-0">
                                <div className="text-lg font-bold text-[#1e3a5f]">{Number(rd.total || 0)}</div>
                                <div className="text-[10px] text-gray-400">คัน</div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Hourly breakdown chart */}
                    {hourlyCounts.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-6">
                        <h2 className="text-lg font-bold text-[#1e3a5f] mb-2">
                          จำนวนรถรายชั่วโมง (วันนี้)
                        </h2>
                        <p className="text-xs text-gray-500 mb-4">
                          ข้อมูลรวมจากกล้องทุกตัว — จำแนกตามประเภทรถ
                        </p>
                        <div className="h-72">
                          <Bar
                            data={{
                              labels: [...new Set(hourlyCounts.map((h) => h.hour))],
                              datasets: [
                                {
                                  label: "รถยนต์",
                                  data: [...new Set(hourlyCounts.map((h) => h.hour))].map((hr) =>
                                    hourlyCounts.filter((h) => h.hour === hr).reduce((s, h) => s + h.car, 0)
                                  ),
                                  backgroundColor: "#5ba8e0",
                                },
                                {
                                  label: "มอเตอร์ไซค์",
                                  data: [...new Set(hourlyCounts.map((h) => h.hour))].map((hr) =>
                                    hourlyCounts.filter((h) => h.hour === hr).reduce((s, h) => s + h.motorcycle, 0)
                                  ),
                                  backgroundColor: "#f59e0b",
                                },
                                {
                                  label: "รถบัส",
                                  data: [...new Set(hourlyCounts.map((h) => h.hour))].map((hr) =>
                                    hourlyCounts.filter((h) => h.hour === hr).reduce((s, h) => s + h.bus, 0)
                                  ),
                                  backgroundColor: "#22c55e",
                                },
                                {
                                  label: "รถบรรทุก",
                                  data: [...new Set(hourlyCounts.map((h) => h.hour))].map((hr) =>
                                    hourlyCounts.filter((h) => h.hour === hr).reduce((s, h) => s + h.truck, 0)
                                  ),
                                  backgroundColor: "#ef4444",
                                },
                              ],
                            }}
                            options={{
                              responsive: true,
                              maintainAspectRatio: false,
                              plugins: { legend: { labels: { color: "#374151" } } },
                              scales: {
                                x: { stacked: true, ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" } },
                                y: { stacked: true, ticks: { color: "#6b7280" }, grid: { color: "#e5e7eb" } },
                              },
                            }}
                          />
                        </div>
                      </div>
                    )}

                    {/* Per-road hourly table */}
                    {hourlyCounts.length > 0 && (
                      <div className="bg-white rounded-xl border border-gray-200 p-6">
                        <h2 className="text-lg font-bold text-[#1e3a5f] mb-4">
                          ข้อมูลย่อย — จำนวนรถแต่ละถนนแต่ละชั่วโมง
                        </h2>
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="bg-gray-50 text-gray-600">
                                <th className="text-left py-2 px-3">เวลา</th>
                                <th className="text-left py-2 px-3">ถนน</th>
                                <th className="text-right py-2 px-2">🚗</th>
                                <th className="text-right py-2 px-2">🏍️</th>
                                <th className="text-right py-2 px-2">🚌</th>
                                <th className="text-right py-2 px-2">🚛</th>
                                <th className="text-right py-2 px-3">รวม</th>
                                <th className="text-right py-2 px-3">ความเร็ว</th>
                              </tr>
                            </thead>
                            <tbody>
                              {hourlyCounts.map((r, i) => (
                                <tr key={i} className="border-t border-gray-100 hover:bg-gray-50">
                                  <td className="py-1.5 px-3 text-gray-500 font-mono text-xs">{r.hour}</td>
                                  <td className="py-1.5 px-3 text-gray-700">{r.road_name}</td>
                                  <td className="py-1.5 px-2 text-right font-mono">{r.car}</td>
                                  <td className="py-1.5 px-2 text-right font-mono">{r.motorcycle}</td>
                                  <td className="py-1.5 px-2 text-right font-mono">{r.bus}</td>
                                  <td className="py-1.5 px-2 text-right font-mono">{r.truck}</td>
                                  <td className="py-1.5 px-3 text-right font-bold">{r.total}</td>
                                  <td className="py-1.5 px-3 text-right text-gray-500">
                                    {(r as HourlyVehicleCount & { avg_speed?: number }).avg_speed
                                      ? `${(r as HourlyVehicleCount & { avg_speed?: number }).avg_speed} km/h`
                                      : "—"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* ========= ดัชนีรถติด ========= */}
                {tab === "index" && (
                  <div className="space-y-6">
                    <div className="bg-white rounded-xl border border-gray-200 p-6">
                      <h2 className="text-lg font-bold text-[#1e3a5f] mb-2">
                        ดัชนีการจราจร (Traffic Index)
                      </h2>
                      <p className="text-sm text-gray-500 mb-4">
                        ดัชนีคำนวณจากอัตราส่วนความเร็วจริงต่อความเร็วอิสระ
                        โดยค่า 1-3 คล่องตัว, 4-6 หนาแน่น, 7-8 ติดขัด, 9-10
                        ติดขัดมาก
                      </p>
                      <div className="h-72">
                        <Line data={chartData} options={chartOptions} />
                      </div>
                    </div>

                    {/* Weekly table */}
                    <div className="bg-white rounded-xl border border-gray-200 p-6">
                      <h3 className="font-semibold text-[#1e3a5f] mb-3">
                        สถิติรายสัปดาห์
                      </h3>
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="bg-gray-50 text-gray-600">
                            <th className="text-left py-2 px-3">วันที่</th>
                            <th className="text-center py-2 px-3">
                              ค่าสูงสุด
                            </th>
                            <th className="text-center py-2 px-3">
                              ค่าเฉลี่ย
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {displayedWeekly.map((w, i) => (
                            <tr
                              key={i}
                              className="border-t border-gray-100 hover:bg-gray-50"
                            >
                              <td className="py-2 px-3 text-gray-700">
                                {w.date}
                              </td>
                              <td className="py-2 px-3 text-center">
                                <span
                                  className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${indexColor(w.max_index)}`}
                                >
                                  {w.max_index.toFixed(1)}
                                </span>
                              </td>
                              <td className="py-2 px-3 text-center">
                                <span
                                  className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${indexColor(w.avg_index)}`}
                                >
                                  {w.avg_index.toFixed(1)}
                                </span>
                              </td>
                            </tr>
                          ))}
                          {displayedWeekly.length === 0 && (
                            <tr>
                              <td
                                colSpan={3}
                                className="py-8 text-center text-gray-400"
                              >
                                ไม่มีข้อมูล
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* ========= สถิติประจำปี ========= */}
                {tab === "yearly" && (
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <h2 className="text-lg font-bold text-[#1e3a5f] mb-4">
                      สถิติประจำปี {year + 543}
                    </h2>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-gray-50 text-gray-600">
                          <th className="text-center py-2 px-3 w-16">ลำดับ</th>
                          <th className="text-left py-2 px-3">วันที่</th>
                          <th className="text-center py-2 px-3">เวลา</th>
                          <th className="text-center py-2 px-3">
                            ดัชนีสูงสุด
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {displayedYearly.map((row, i) => (
                          <tr
                            key={i}
                            className="border-t border-gray-100 hover:bg-gray-50"
                          >
                            <td className="py-2 px-3 text-center text-gray-500">
                              {i + 1}
                            </td>
                            <td className="py-2 px-3 text-gray-700">
                              {row.date}
                            </td>
                            <td className="py-2 px-3 text-center text-gray-600">
                              {row.time}
                            </td>
                            <td className="py-2 px-3 text-center">
                              <span
                                className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${indexColor(row.max_index)}`}
                              >
                                {row.max_index.toFixed(1)}
                              </span>
                            </td>
                          </tr>
                        ))}
                        {displayedYearly.length === 0 && (
                          <tr>
                            <td
                              colSpan={4}
                              className="py-8 text-center text-gray-400"
                            >
                              ไม่มีข้อมูล
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* ========= จำนวนรถในแต่ละวัน ========= */}
                {tab === "daily" && (
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <h2 className="text-lg font-bold text-[#1e3a5f] mb-4">
                      จำนวนรถในแต่ละวัน ({year + 543})
                    </h2>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-gray-50 text-gray-600">
                          <th className="text-center py-2 px-3 w-16">ลำดับ</th>
                          <th className="text-left py-2 px-3">วันที่</th>
                          <th className="text-right py-2 px-3">
                            จำนวนรถทั้งหมด
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {displayedDailyCount.map((d, i) => (
                          <tr
                            key={i}
                            className="border-t border-gray-100 hover:bg-gray-50"
                          >
                            <td className="py-2 px-3 text-center text-gray-500">
                              {i + 1}
                            </td>
                            <td className="py-2 px-3 text-gray-700">
                              {d.date}
                            </td>
                            <td className="py-2 px-3 text-right font-mono text-gray-800">
                              {d.total_vehicles.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                        {displayedDailyCount.length === 0 && (
                          <tr>
                            <td
                              colSpan={3}
                              className="py-8 text-center text-gray-400"
                            >
                              ไม่มีข้อมูล
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* ========= TOP 10 ถนน ========= */}
                {tab === "top10" && (
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <h2 className="text-lg font-bold text-[#1e3a5f] mb-4">
                      TOP 10 ถนนที่รถติดมากที่สุด ({year + 543})
                    </h2>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="bg-gray-50 text-gray-600">
                          <th className="text-center py-2 px-3 w-16">ลำดับ</th>
                          <th className="text-left py-2 px-3">ชื่อถนน</th>
                          <th className="text-center py-2 px-3">
                            ดัชนีสูงสุดเฉลี่ย
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {displayedTopRoads.slice(0, 10).map((r, i) => (
                          <tr
                            key={i}
                            className="border-t border-gray-100 hover:bg-gray-50"
                          >
                            <td className="py-2 px-3 text-center text-gray-500">
                              {i + 1}
                            </td>
                            <td className="py-2 px-3 text-gray-700">
                              {r.road}
                            </td>
                            <td className="py-2 px-3 text-center">
                              <span
                                className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${indexColor(r.avg_max_index)}`}
                              >
                                {r.avg_max_index.toFixed(1)}
                              </span>
                            </td>
                          </tr>
                        ))}
                        {displayedTopRoads.length === 0 && (
                          <tr>
                            <td
                              colSpan={3}
                              className="py-8 text-center text-gray-400"
                            >
                              ไม่มีข้อมูล
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                )}

              </>
            )}
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
