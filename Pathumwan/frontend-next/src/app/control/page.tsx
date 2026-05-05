"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import {
  getAIStatus,
  setSignalMode,
  setManualSignal,
  getJunctions,
  setSignalPhase,
  getCameras,
} from "@/lib/api";
import type { AIStatus, Junction, Camera } from "@/lib/types";

const CctvFeed = dynamic(() => import("@/components/CctvFeed"), {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center bg-gray-900 rounded-lg aspect-video">
      <div className="animate-spin h-6 w-6 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
    </div>
  ),
});

function looksLikeMachineName(value: string | undefined) {
  const label = String(value || "").trim();
  if (!label) return true;
  return /^(joinedS|cluster_|TLS_|J\d|\d+[_-]\d+)/i.test(label) || (label.includes("_") && /\d/.test(label));
}

function preferredHumanLabel(...values: Array<string | undefined>) {
  for (const value of values) {
    const normalized = String(value || "").trim();
    if (normalized && !looksLikeMachineName(normalized)) {
      return normalized;
    }
  }
  return "";
}

function cameraMatchesJunction(camera: Camera, junction: Junction) {
  const junctionId = String(junction.id || "");
  const junctionCameraId = String(junction.camera_id || "");
  return (
    camera.camera_id === junctionCameraId ||
    String(camera.junction_id || "") === junctionId ||
    String(camera.sumo_tls_id || "") === junctionId
  );
}

export default function ControlPage() {
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [junctions, setJunctions] = useState<Junction[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedJunction, setSelectedJunction] = useState<Junction | null>(null);
  const selectedJunctionRef = useRef<Junction | null>(null);
  const [editDurations, setEditDurations] = useState<Record<number, number>>({});
  const [junctionSearch, setJunctionSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");

  // Keep ref in sync so the stable fetchData can read latest selection
  useEffect(() => { selectedJunctionRef.current = selectedJunction; }, [selectedJunction]);

  const fetchData = useCallback(async () => {
    try {
      const [ai, js, cs] = await Promise.all([
        getAIStatus(),
        getJunctions(),
        getCameras(),
      ]);
      setAiStatus(ai);
      setJunctions(js as Junction[]);
      setCameras(cs as Camera[]);
      const current = selectedJunctionRef.current;
      if (!current && js.length > 0) {
        const initialJunction =
          js.find((j: Junction) => {
            const matchedCamera = (cs as Camera[]).find((camera) => cameraMatchesJunction(camera, j));
            return Boolean(matchedCamera?.camera_id || j.camera_id);
          }) || js[0];
        setSelectedJunction(initialJunction);
        initEditDurations(initialJunction);
      } else if (current) {
        const updated = js.find((j: Junction) => j.id === current.id);
        if (updated) {
          setSelectedJunction(updated);
        }
      }
    } catch {
      /* offline */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  function initEditDurations(j: Junction) {
    const d: Record<number, number> = {};
    j.phases.forEach((p) => {
      d[p.index] = Math.round(p.duration);
    });
    setEditDurations(d);
  }

  function selectJunction(j: Junction) {
    setSelectedJunction(j);
    initEditDurations(j);
  }

  async function toggleMode() {
    if (!aiStatus || toggling) return;
    setToggling(true);
    const newMode = aiStatus.mode === "ai" ? "manual" : "ai";
    try {
      await setSignalMode(newMode);
      setAiStatus({ ...aiStatus, mode: newMode });
      showToast(newMode === "ai" ? "เปิดโหมด AI สำเร็จ" : "เปลี่ยนเป็นโหมดควบคุมเอง");
    } catch {
      showToast("เกิดข้อผิดพลาด");
    } finally {
      setToggling(false);
    }
  }

  async function handleSignal(state: string) {
    if (!selectedJunction) return;
    try {
      await setManualSignal(selectedJunction.id, state);
      showToast(`เปลี่ยนไฟ ${selectedJunction.name} เป็น ${state}`);
      fetchData();
    } catch {
      showToast("เกิดข้อผิดพลาด");
    }
  }

  async function handleSavePhases() {
    if (!selectedJunction || saving) return;
    setSaving(true);
    try {
      const phases = Object.entries(editDurations).map(([idx, dur]) => ({
        index: parseInt(idx),
        duration: dur,
      }));
      await setSignalPhase(selectedJunction.id, phases);
      showToast(`บันทึกเฟสไฟจราจร ${selectedJunction.name} สำเร็จ`);
      fetchData();
    } catch {
      showToast("เกิดข้อผิดพลาด");
    } finally {
      setSaving(false);
    }
  }

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(""), 3000);
  }

  function getJunctionLabel(junction: Junction, junctionCamera?: Camera | null) {
    if (junctionCamera?.name && !looksLikeMachineName(junctionCamera.name)) {
      return junctionCamera.name;
    }
    const humanLabel = preferredHumanLabel(
      junctionCamera?.junction,
      junction.name !== junction.id ? junction.name : "",
    );
    if (humanLabel) {
      return humanLabel;
    }
    if (junctionCamera?.road) {
      return `แยก ${junctionCamera.road}`;
    }
    const cleanId = String(junction.id).replace(/^(joinedS_|TLS_|cluster_)/i, '');
    return `ทางร่วมทางแยก ${cleanId}`;
  }

  function getJunctionSubtitle(junction: Junction, junctionCamera?: Camera | null) {
    if (junctionCamera?.road) {
      return `ถนน ${junctionCamera.road}`;
    }
    const humanSecondary = preferredHumanLabel(
      junctionCamera?.junction,
      junction.name !== junction.id ? junction.name : "",
    );
    if (humanSecondary) {
      return humanSecondary;
    }
    if (junction.camera_id && !looksLikeMachineName(junction.camera_id)) {
      return junction.camera_id;
    }
    const cleanId = String(junction.id).replace(/^(joinedS_|TLS_|cluster_)/i, '');
    return `จุดตัดรหัส ${cleanId}`;
  }

  const filteredJunctions = junctions
    .filter((j) => {
      // Drop junctions whose only available name is machine-generated (cluster_*, TLS_*, J1234, 42_12…).
      const junctionCamera = cameras.find((camera) => cameraMatchesJunction(camera, j));
      const serverName = String(j.display_name_th || j.name || "").trim();
      if (serverName && !looksLikeMachineName(serverName)) return true;
      const camName = junctionCamera?.name || "";
      if (camName && !looksLikeMachineName(camName)) return true;
      const camJunction = junctionCamera?.junction || "";
      if (camJunction && !looksLikeMachineName(camJunction)) return true;
      return false;
    })
    .filter((j) => {
      const junctionCamera = cameras.find((camera) => cameraMatchesJunction(camera, j));
      const label = getJunctionLabel(j, junctionCamera);
      return (
        !junctionSearch ||
        label.toLowerCase().includes(junctionSearch.toLowerCase()) ||
        j.id.toLowerCase().includes(junctionSearch.toLowerCase()) ||
        j.camera_id.toLowerCase().includes(junctionSearch.toLowerCase())
      );
    });

  const selectedCam = selectedJunction
    ? cameras.find((camera) => cameraMatchesJunction(camera, selectedJunction))
    : null;

  const feedCameraId = selectedCam?.camera_id || selectedJunction?.camera_id || "";
  const feedCameraName = selectedJunction
    ? getJunctionLabel(selectedJunction, selectedCam)
    : (selectedCam?.name || "CCTV");
  const feedSubtitle = selectedJunction
    ? getJunctionSubtitle(selectedJunction, selectedCam)
    : (selectedCam?.road || selectedCam?.junction || "");

  const phaseTypeLabel = (type: string) => {
    switch (type) {
      case "green": return "เขียว (ปล่อยรถ)";
      case "yellow": return "เหลือง (เตือน)";
      case "red": return "แดง (หยุด)";
      default: return type;
    }
  };

  const phaseColor = (type: string) => {
    switch (type) {
      case "green": return "#22c55e";
      case "yellow": return "#eab308";
      case "red": return "#ef4444";
      default: return "#94a3b8";
    }
  };

  return (
    <ProtectedRoute adminOnly>
      <div className="h-screen flex flex-col">
        <Navbar />
        <div className="flex flex-1 pt-14 overflow-hidden bg-gray-50 justify-center gap-5 px-5 pb-5">
          {/* Left — Junction list + camera */}
          <div className="my-5 flex w-[min(44vw,580px)] shrink-0 flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
            {/* Search */}
            <div className="px-4 py-3 border-b border-gray-200">
              <input
                type="text"
                value={junctionSearch}
                onChange={(e) => setJunctionSearch(e.target.value)}
                placeholder="ค้นหาแยกจราจร..."
                className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 placeholder-gray-400 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
              />
            </div>

            {/* Junction list */}
            <div className="min-h-[240px] max-h-[360px] overflow-y-auto overflow-x-hidden border-b border-gray-200">
              {filteredJunctions.map((j) => {
                const g = j.current_state.toLowerCase().split("").filter((c) => c === "g").length;
                const total = Math.max(1, j.current_state.length);
                const dom = g / total > 0.3 ? "green" : j.current_state.includes("y") ? "yellow" : "red";
                const junctionCamera = cameras.find((camera) => cameraMatchesJunction(camera, j));
                const junctionLabel = getJunctionLabel(j, junctionCamera);
                const junctionSubtitle = getJunctionSubtitle(j, junctionCamera);
                return (
                  <button
                    key={j.id}
                    onClick={() => selectJunction(j)}
                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-blue-50 ${
                      selectedJunction?.id === j.id ? "bg-blue-50" : ""
                    }`}
                  >
                    <span
                      className="w-3 h-3 rounded-full flex-shrink-0"
                      style={{ backgroundColor: phaseColor(dom) }}
                    />
                    <div className="flex-1 min-w-0 overflow-hidden">
                      <div className="truncate text-sm font-medium leading-5 text-[#1e3a5f]">{junctionLabel}</div>
                      <div className="mt-0.5 text-[10px] text-gray-400 truncate">
                        {junctionSubtitle}
                      </div>
                    </div>
                    <div className="text-right flex-shrink-0">
                      <div className="text-[10px] text-gray-500">เปลี่ยนใน</div>
                      <div className="text-xs font-mono text-[#5ba8e0]">{Math.round(j.time_to_switch)}s</div>
                    </div>
                  </button>
                );
              })}
              {filteredJunctions.length === 0 && (
                <div className="py-8 text-center text-gray-400 text-sm">ไม่พบแยกจราจร</div>
              )}
            </div>

            <div className="border-t border-gray-200 bg-gray-50 px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-400">Camera feed</div>
                  <div className="mt-1 truncate text-sm font-semibold leading-5 text-[#1e3a5f]">
                    {feedCameraId ? feedCameraName : "เลือกแยกจราจรเพื่อดูกล้อง"}
                  </div>
                  {feedSubtitle && <div className="mt-1 text-[11px] text-gray-500">{feedSubtitle}</div>}
                </div>
                {feedCameraId && (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                    {feedCameraId}
                  </span>
                )}
              </div>
            </div>
            <div className="relative min-h-[360px] flex-1 bg-gray-900">
              {feedCameraId ? (
                <CctvFeed
                  cameraId={feedCameraId}
                  cameraName={feedCameraName}
                  subtitle={feedSubtitle || undefined}
                  detectMode={false}
                  cameraLat={selectedCam?.lat}
                  cameraLng={selectedCam?.lng}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500 text-sm">
                  {selectedJunction ? "ไม่มีกล้องสำหรับแยกนี้" : "เลือกแยกจราจร"}
                </div>
              )}
            </div>
          </div>

          {/* Right — Controls */}
          <div className="flex-1 overflow-y-auto py-5">
            {loading ? (
              <div className="flex justify-center py-20">
                <div className="animate-spin h-8 w-8 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
              </div>
            ) : (
              <div className="mx-auto max-w-4xl space-y-5">
                {/* AI Mode Toggle */}
                <div className="bg-white rounded-xl border border-gray-200 p-6">
                  <h2 className="text-lg font-bold text-[#1e3a5f] mb-4">โหมดควบคุมสัญญาณ</h2>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm text-gray-600">
                        {aiStatus?.mode === "ai" ? "AI กำลังควบคุมสัญญาณอัตโนมัติ" : "ควบคุมด้วยตนเอง (Manual)"}
                      </p>
                      <p className="text-xs text-gray-400 mt-1">
                        {aiStatus?.mode === "ai"
                          ? "ระบบปรับเฟสไฟจราจรอัตโนมัติตามความหนาแน่นของรถ"
                          : "ผู้ดูแลสามารถตั้งค่าเฟสไฟจราจรได้เอง"}
                      </p>
                    </div>
                    <button onClick={toggleMode} disabled={toggling} className="flex items-center gap-2">
                      <span className={`text-sm font-medium ${aiStatus?.mode === "ai" ? "text-green-600" : "text-gray-500"}`}>
                        {aiStatus?.mode === "ai" ? "AI" : "Manual"}
                      </span>
                      <div className={`relative w-14 h-7 rounded-full transition-colors ${aiStatus?.mode === "ai" ? "bg-green-500" : "bg-gray-300"}`}>
                        <span className={`absolute top-0.5 w-6 h-6 bg-white rounded-full shadow transition-transform ${aiStatus?.mode === "ai" ? "translate-x-7" : "translate-x-0.5"}`} />
                      </div>
                    </button>
                  </div>
                </div>

                {/* Quick Signal Override */}
                {selectedJunction && (
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <div className="flex items-center justify-between mb-4">
                      <h2 className="text-lg font-bold text-[#1e3a5f]">สลับสัญญาณไฟด่วน</h2>
                      <span className="max-w-[45%] truncate rounded bg-gray-100 px-2 py-1 text-xs text-gray-400">
                        {getJunctionSubtitle(selectedJunction, selectedCam)}
                      </span>
                    </div>
                    <p className="mb-4 text-sm text-gray-500">{getJunctionLabel(selectedJunction, selectedCam)}</p>

                    {/* Current state visualization */}
                    <div className="flex items-center gap-2 mb-4 p-3 bg-gray-50 rounded-lg">
                      <span className="text-xs text-gray-500 mr-2">สถานะปัจจุบัน:</span>
                      <div className="flex gap-0.5">
                        {selectedJunction.current_state.slice(0, 20).split("").map((ch, i) => (
                          <div
                            key={i}
                            className="w-3 h-6 rounded-sm"
                            style={{
                              backgroundColor:
                                ch === "G" || ch === "g" ? "#22c55e" : ch === "y" || ch === "Y" ? "#eab308" : "#ef4444",
                            }}
                            title={`Lane ${i + 1}: ${ch}`}
                          />
                        ))}
                      </div>
                      <span className="text-xs text-gray-400 ml-2">เปลี่ยนใน {Math.round(selectedJunction.time_to_switch)}s</span>
                    </div>

                    {/* Quick override buttons */}
                    <div className="flex gap-3">
                      {(
                        [
                          ["red", "#ef4444", "หยุดทั้งหมด"],
                          ["yellow", "#eab308", "เหลืองทั้งหมด"],
                          ["green", "#22c55e", "เขียวทั้งหมด"],
                        ] as const
                      ).map(([state, color, label]) => (
                        <button
                          key={state}
                          onClick={() => handleSignal(state)}
                          disabled={aiStatus?.mode === "ai"}
                          className="flex-1 flex flex-col items-center gap-2 py-3 rounded-lg border-2 transition-all hover:shadow-md disabled:opacity-30 disabled:cursor-not-allowed"
                          style={{ borderColor: color }}
                        >
                          <div className="w-8 h-8 rounded-full" style={{ backgroundColor: color }} />
                          <span className="text-xs font-medium text-gray-600">{label}</span>
                        </button>
                      ))}
                    </div>
                    {aiStatus?.mode === "ai" && (
                      <p className="text-xs text-amber-500 mt-2">* ปิดโหมด AI ก่อนเพื่อควบคุมเอง</p>
                    )}
                  </div>
                )}

                {/* Phase Timing Editor */}
                {selectedJunction && selectedJunction.phases.length > 0 && (
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <h2 className="text-lg font-bold text-[#1e3a5f] mb-2">ตั้งค่าเฟสสัญญาณไฟ</h2>
                    <p className="text-xs text-gray-400 mb-4">
                      ปรับระยะเวลาของแต่ละเฟส (วินาที) — ทั่วไปครบรอบ 60-120s
                    </p>

                    {/* Phase cycle visualization */}
                    <div className="mb-4 p-3 bg-gray-50 rounded-lg">
                      <div className="flex items-center gap-1 h-8">
                        {selectedJunction.phases.map((p) => {
                          const dur = editDurations[p.index] ?? p.duration;
                          const totalDur = Object.values(editDurations).reduce((a, b) => a + b, 0) || 1;
                          const pct = (dur / totalDur) * 100;
                          return (
                            <div
                              key={p.index}
                              className="h-full rounded-sm relative flex items-center justify-center"
                              style={{
                                width: `${Math.max(pct, 5)}%`,
                                backgroundColor: phaseColor(p.type),
                                opacity: selectedJunction.current_phase === p.index ? 1 : 0.5,
                              }}
                            >
                              <span className="text-[9px] text-white font-bold">{dur}s</span>
                            </div>
                          );
                        })}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        รวม: {Object.values(editDurations).reduce((a, b) => a + b, 0)}s ต่อรอบ
                      </div>
                    </div>

                    {/* Phase editors */}
                    <div className="space-y-3">
                      {selectedJunction.phases.map((p) => (
                        <div
                          key={p.index}
                          className={`flex items-center gap-4 p-3 rounded-lg border ${
                            selectedJunction.current_phase === p.index
                              ? "border-blue-300 bg-blue-50"
                              : "border-gray-200 bg-white"
                          }`}
                        >
                          <div
                            className="w-4 h-10 rounded-sm flex-shrink-0"
                            style={{ backgroundColor: phaseColor(p.type) }}
                          />
                          <div className="flex-1">
                            <div className="text-sm font-medium text-gray-700">
                              เฟส {p.index + 1}: {phaseTypeLabel(p.type)}
                            </div>
                            <div className="text-[10px] text-gray-400 font-mono truncate">
                              {p.state.slice(0, 30)}{p.state.length > 30 ? "..." : ""}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 flex-shrink-0">
                            <button
                              onClick={() =>
                                setEditDurations((prev) => ({
                                  ...prev,
                                  [p.index]: Math.max(3, (prev[p.index] ?? p.duration) - 5),
                                }))
                              }
                              disabled={aiStatus?.mode === "ai"}
                              className="w-7 h-7 rounded-md bg-gray-100 text-gray-600 text-sm font-bold hover:bg-gray-200 disabled:opacity-30"
                            >
                              -
                            </button>
                            <input
                              type="number"
                              min={3}
                              max={120}
                              value={editDurations[p.index] ?? Math.round(p.duration)}
                              onChange={(e) =>
                                setEditDurations((prev) => ({
                                  ...prev,
                                  [p.index]: Math.max(3, Math.min(120, parseInt(e.target.value) || 3)),
                                }))
                              }
                              disabled={aiStatus?.mode === "ai"}
                              className="w-16 text-center border border-gray-300 rounded-md py-1 text-sm font-mono disabled:opacity-30"
                            />
                            <button
                              onClick={() =>
                                setEditDurations((prev) => ({
                                  ...prev,
                                  [p.index]: Math.min(120, (prev[p.index] ?? p.duration) + 5),
                                }))
                              }
                              disabled={aiStatus?.mode === "ai"}
                              className="w-7 h-7 rounded-md bg-gray-100 text-gray-600 text-sm font-bold hover:bg-gray-200 disabled:opacity-30"
                            >
                              +
                            </button>
                            <span className="text-xs text-gray-400 w-4">s</span>
                          </div>
                        </div>
                      ))}
                    </div>

                    {/* Save button */}
                    <div className="mt-4 flex items-center gap-3">
                      <button
                        onClick={handleSavePhases}
                        disabled={aiStatus?.mode === "ai" || saving}
                        className="px-6 py-2.5 bg-[#5ba8e0] text-white rounded-lg text-sm font-medium hover:bg-[#4a93c8] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        {saving ? "กำลังบันทึก..." : "บันทึกการตั้งค่า"}
                      </button>
                      <button
                        onClick={() => selectedJunction && initEditDurations(selectedJunction)}
                        className="px-4 py-2.5 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200"
                      >
                        รีเซ็ต
                      </button>
                    </div>
                  </div>
                )}

                {/* Info */}
                <div className="bg-blue-50 rounded-xl border border-blue-200 p-4 text-sm text-blue-700">
                  <p className="font-semibold mb-1">คำแนะนำการตั้งค่า</p>
                  <ul className="text-xs space-y-1 text-blue-600">
                    <li>• รอบสัญญาณไฟทั่วไปของ กทม. อยู่ที่ 60-120 วินาที</li>
                    <li>• เฟสเหลือง (เตือน) ควรอยู่ที่ 3-5 วินาที</li>
                    <li>• เฟสแดงทั้งหมด (clearance) ควรอยู่ที่ 2-3 วินาที</li>
                    <li>• เฟสเขียวตามทิศทางหลัก ควรอยู่ที่ 15-45 วินาที</li>
                  </ul>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Toast */}
        {toast && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-gray-800 text-white px-5 py-2.5 rounded-lg shadow-lg text-sm z-50 animate-fade-in">
            {toast}
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
