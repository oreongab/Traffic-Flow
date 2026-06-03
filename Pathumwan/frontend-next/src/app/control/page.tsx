"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuth } from "@/lib/auth";
import {
  getAIStatus,
  getAIAlgorithms,
  getAIDecisionHistory,
  getAdminLogs,
  setSignalMode,
  setAIAlgorithm,
  setManualSignal,
  getJunctions,
  setSignalPhase,
  getCameras,
} from "@/lib/api";
import type {
  AIAlgorithmOption,
  AIDecision,
  AIDecisionHistoryEntry,
  AIStatus,
  Camera,
  Junction,
  SystemLogEntry,
} from "@/lib/types";

type ManualDirection = "all" | "ns" | "ew";

const manualDirectionOptions: Array<{ value: ManualDirection; label: string; hint: string }> = [
  { value: "all", label: "ทั้งแยก", hint: "สลับทุกแนวของสี่แยกพร้อมกัน" },
  { value: "ns", label: "เหนือ-ใต้", hint: "คุมเฉพาะแนวเหนือ-ใต้" },
  { value: "ew", label: "ตะวันออก-ตก", hint: "คุมเฉพาะแนวตะวันออก-ตก" },
];

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

function buildCameraJunctionFallback(camera: Camera): Junction {
  return {
    id: `camera:${camera.camera_id}`,
    name: camera.display_name || camera.name || camera.camera_id,
    display_name_th: camera.display_name || camera.name || camera.junction || camera.camera_id,
    camera_id: camera.camera_id,
    lat: camera.lat,
    lng: camera.lng,
    current_state: "rrrr",
    current_phase: 0,
    time_to_switch: 0,
    phases: [],
  };
}

function isCameraFallbackJunction(junction: Junction | null | undefined) {
  return String(junction?.id || "").startsWith("camera:");
}

export default function ControlPage() {
  const { user } = useAuth();
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [availableAlgorithms, setAvailableAlgorithms] = useState<AIAlgorithmOption[]>([]);
  const [decisionHistory, setDecisionHistory] = useState<AIDecisionHistoryEntry[]>([]);
  const [controlLogs, setControlLogs] = useState<SystemLogEntry[]>([]);
  const [junctions, setJunctions] = useState<Junction[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selectedJunction, setSelectedJunction] = useState<Junction | null>(null);
  const selectedJunctionRef = useRef<Junction | null>(null);
  const [editDurations, setEditDurations] = useState<Record<number, number>>({});
  const [selectedDirection, setSelectedDirection] = useState<ManualDirection>("all");
  const [junctionSearch, setJunctionSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [changingAlgorithm, setChangingAlgorithm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const isAdmin = user?.role === "admin";

  // Keep ref in sync so the stable fetchData can read latest selection
  useEffect(() => { selectedJunctionRef.current = selectedJunction; }, [selectedJunction]);

  const fetchData = useCallback(async () => {
    try {
      const [aiResult, junctionResult, cameraResult, algorithmResult, decisionResult, logResult] = await Promise.allSettled([
        getAIStatus(),
        getJunctions(),
        getCameras(),
        getAIAlgorithms(),
        getAIDecisionHistory(24),
        getAdminLogs(),
      ]);
      const ai = aiResult.status === "fulfilled" ? aiResult.value : null;
      const js = junctionResult.status === "fulfilled" ? (junctionResult.value as Junction[]) : [];
      const cs = cameraResult.status === "fulfilled" ? (cameraResult.value as Camera[]) : [];
      const algoPayload = algorithmResult.status === "fulfilled" ? algorithmResult.value : { algorithms: [] };
      const dbDecisions = decisionResult.status === "fulfilled" ? decisionResult.value : [];
      const logs = logResult.status === "fulfilled" ? logResult.value : [];

      if (ai) setAiStatus(ai);
      if (junctionResult.status === "fulfilled") setJunctions(js);
      if (cameraResult.status === "fulfilled") setCameras(cs);
      if (algorithmResult.status === "fulfilled") setAvailableAlgorithms(algoPayload.algorithms);
      if (decisionResult.status === "fulfilled") setDecisionHistory(dbDecisions);
      if (logResult.status === "fulfilled") setControlLogs(logs);
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
    if (!isAdmin) {
      showToast("บัญชีนี้ดูได้อย่างเดียว ต้องใช้บัญชีเจ้าหน้าที่เพื่อสลับโหมด");
      return;
    }
    if (!aiStatus || toggling) return;
    setToggling(true);
    const newMode = aiStatus.mode === "ai" ? "manual" : "ai";
    try {
      await setSignalMode(newMode);
      setAiStatus({ ...aiStatus, mode: newMode });
      showToast(newMode === "ai" ? "เปิดโหมด AI สำเร็จ" : "เปลี่ยนเป็นโหมดควบคุมเอง");
      fetchData();
    } catch {
      showToast("เกิดข้อผิดพลาด");
    } finally {
      setToggling(false);
    }
  }

  async function handleAlgorithmChange(algorithm: string) {
    if (!isAdmin) {
      showToast("บัญชีนี้ดูได้อย่างเดียว ต้องใช้บัญชีเจ้าหน้าที่เพื่อสลับโมเดล");
      return;
    }
    if (!algorithm || changingAlgorithm) return;
    setChangingAlgorithm(true);
    try {
      await setAIAlgorithm(algorithm);
      setAiStatus((prev) => (prev ? { ...prev, algorithm } : prev));
      showToast(`เปลี่ยน AI เป็น ${algorithm} สำเร็จ`);
      fetchData();
    } catch {
      showToast("เปลี่ยน AI ไม่สำเร็จ");
    } finally {
      setChangingAlgorithm(false);
    }
  }

  async function handleSignal(state: string) {
    if (!isAdmin) {
      showToast("บัญชีนี้ดูได้อย่างเดียว ต้องใช้บัญชีเจ้าหน้าที่เพื่อควบคุม manual");
      return;
    }
    if (!selectedJunction) return;
    try {
      await setManualSignal(selectedJunction.id, state, selectedDirection);
      showToast(`เปลี่ยนไฟ ${getJunctionLabel(selectedJunction, selectedCam)} เป็น ${state}`);
      fetchData();
    } catch {
      showToast("เกิดข้อผิดพลาด");
    }
  }

  async function handleSavePhases() {
    if (!isAdmin) {
      showToast("บัญชีนี้ดูได้อย่างเดียว ต้องใช้บัญชีเจ้าหน้าที่เพื่อบันทึกเฟส");
      return;
    }
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

  const researchCameraIds = new Set(
    cameras
      .filter((camera) => camera.research_target)
      .map((camera) => String(camera.camera_id || ""))
      .filter(Boolean)
  );
  const expectedResearchCount = researchCameraIds.size
    || ((aiStatus?.pathumwan_research_junction_count ?? 14) + (aiStatus?.ratchathewi_feeder_junction_count ?? 2));

  const controlCameraRoster = [...(cameras.some((camera) => camera.research_target)
    ? cameras.filter((camera) => camera.research_target)
    : cameras
  )].sort((a, b) => (a.research_order ?? 9999) - (b.research_order ?? 9999));

  const controlJunctions = controlCameraRoster.map((camera) => (
    junctions.find((junction) => cameraMatchesJunction(camera, junction)) || buildCameraJunctionFallback(camera)
  ));

  const filteredJunctions = controlJunctions.filter((j) => {
    const junctionCamera = cameras.find((camera) => cameraMatchesJunction(camera, j));
    const label = getJunctionLabel(j, junctionCamera);
    const subtitle = getJunctionSubtitle(j, junctionCamera);
    const needle = junctionSearch.toLowerCase();
    return (
      !junctionSearch ||
      label.toLowerCase().includes(needle) ||
      subtitle.toLowerCase().includes(needle) ||
      j.id.toLowerCase().includes(needle) ||
      j.camera_id.toLowerCase().includes(needle)
    );
  });
  const runtimeReady = Boolean(
    aiStatus?.runtime_ready || aiStatus?.simulation_active || filteredJunctions.length > 0
  );
  const systemModeLabel = aiStatus?.backends?.system_mode === "real"
    ? "ตรวจจับจริง"
    : "จำลองเสมือนจริง";
  const cameraBackendLabel = aiStatus?.backends?.camera_backend === "rtsp"
    ? "กล้องจริง / RTSP"
    : "SUMO + YOLO";

  const selectedCam = selectedJunction
    ? cameras.find((camera) => cameraMatchesJunction(camera, selectedJunction))
    : null;
  const selectedSignalReady = Boolean(selectedJunction && !isCameraFallbackJunction(selectedJunction));

  const feedCameraId = selectedCam?.camera_id || selectedJunction?.camera_id || "";
  const feedCameraName = selectedJunction
    ? getJunctionLabel(selectedJunction, selectedCam)
    : (selectedCam?.name || "CCTV");
  const feedSubtitle = selectedJunction
    ? getJunctionSubtitle(selectedJunction, selectedCam)
    : (selectedCam?.road || selectedCam?.junction || "");

  const phaseColor = (type: string) => {
    switch (type) {
      case "green": return "#22c55e";
      case "yellow": return "#eab308";
      case "red": return "#ef4444";
      default: return "#94a3b8";
    }
  };

  function formatDecisionTimestamp(value: number | string | undefined | null) {
    if (value === undefined || value === null || value === "") return "—";
    const parsed = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleString("th-TH");
  }

  function getJunctionNameById(junctionId: string) {
    const found = junctions.find((junction) => String(junction.id) === String(junctionId));
    if (!found) return junctionId;
    const junctionCamera = cameras.find((camera) => cameraMatchesJunction(camera, found));
    return getJunctionLabel(found, junctionCamera);
  }

  const liveAIDecisions: AIDecision[] = aiStatus?.last_decisions || [];
  const recentControlLogs = controlLogs.slice(0, 8);
  const recentDbDecisions = decisionHistory.slice(0, 8);

  return (
    <ProtectedRoute>
      <div className="h-screen flex flex-col">
        <Navbar />
        <div className="flex flex-1 pt-14 overflow-hidden bg-gray-50 gap-5 px-5 pb-5">
          {/* Left — Junction list + camera */}
          <div className="my-5 flex w-[min(56vw,820px)] shrink-0 flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
            {/* Search */}
            <div className="px-4 py-3 border-b border-gray-200">
              <input
                type="text"
                value={junctionSearch}
                onChange={(e) => setJunctionSearch(e.target.value)}
                placeholder="ค้นหาแยกจราจร..."
                className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 placeholder-gray-400 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
              />
              <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-gray-500">
                <span>รายการ CCTV ชุดงานวิจัย</span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 font-semibold text-slate-600">
                  {filteredJunctions.length}/{expectedResearchCount} จุด
                </span>
              </div>
            </div>

            {/* Junction list */}
            <div className="min-h-[150px] max-h-[220px] overflow-y-auto overflow-x-hidden border-b border-gray-200">
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
            <div className="relative min-h-[420px] flex-1 bg-gray-900 xl:min-h-[520px]">
              {feedCameraId ? (
                <CctvFeed
                  cameraId={feedCameraId}
                  cameraName={feedCameraName}
                  subtitle={feedSubtitle || undefined}
                  detectMode={false}
                  cameraLat={selectedCam?.lat}
                  cameraLng={selectedCam?.lng}
                  showCounts={false}
                  showMiniMap={false}
                  streamFps={4}
                  streamMode="detect"
                  showInfoOverlay={false}
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
                  <div className="flex flex-col gap-5">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-400">Control mode</p>
                        <p className="text-sm text-gray-600">
                          {aiStatus?.mode === "ai" ? "AI กำลังควบคุมสัญญาณอัตโนมัติ" : "ควบคุมด้วยตนเอง (Manual)"}
                        </p>
                        <p className="text-xs text-gray-400 mt-1">
                          {aiStatus?.mode === "ai"
                            ? "รองรับการสลับโมเดล PPO, DQN และ A2C บน runtime จำลองเสมือนจริงที่วิเคราะห์จาก YOLO และสถานะสัญญาณ"
                            : "ผู้ดูแลสามารถ override manual และปรับ phase plan ได้เมื่อ runtime พร้อม"}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                          <span className={`rounded-full px-2 py-1 font-semibold ${runtimeReady ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                            {runtimeReady ? "Runtime พร้อม" : "Runtime รอข้อมูล"}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2 py-1 font-semibold text-slate-600">
                            โหมดระบบ: {systemModeLabel}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2 py-1 font-semibold text-slate-600">
                            แหล่งภาพ: {cameraBackendLabel}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2 py-1 font-semibold text-slate-600">
                            AI ปัจจุบัน: {aiStatus?.algorithm || "PPO"}
                          </span>
                          <span className="rounded-full bg-slate-100 px-2 py-1 font-semibold text-slate-600">
                            แยก AI: {aiStatus?.research_junction_count || junctions.length} ({aiStatus?.pathumwan_research_junction_count ?? 14}+{aiStatus?.ratchathewi_feeder_junction_count ?? 2})
                          </span>
                        </div>
                      </div>
                      <button
                        onClick={toggleMode}
                        disabled={toggling || !isAdmin}
                        className="flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <span className={`text-sm font-medium ${aiStatus?.mode === "ai" ? "text-green-600" : "text-gray-500"}`}>
                          {aiStatus?.mode === "ai" ? "AI" : "Manual"}
                        </span>
                        <div className={`relative w-11 h-6 rounded-full transition-colors ${aiStatus?.mode === "ai" ? "bg-green-500" : "bg-gray-300"}`}>
                          <span className={`absolute top-[2px] left-[2px] w-5 h-5 bg-white rounded-full shadow transition-transform ${aiStatus?.mode === "ai" ? "translate-x-5" : "translate-x-0"}`} />
                        </div>
                      </button>
                    </div>

                    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-sm font-semibold text-[#1e3a5f]">เลือก AI algorithm</div>
                          <div className="mt-1 text-xs text-gray-500">สำหรับงานวิจัยสามารถสลับโมเดลระหว่าง PPO, DQN และ A2C ได้ทันทีบน runtime ปัจจุบัน</div>
                        </div>
                        <select
                          value={aiStatus?.algorithm || "PPO"}
                          onChange={(event) => handleAlgorithmChange(event.target.value)}
                          disabled={changingAlgorithm || !isAdmin}
                          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700"
                        >
                          {availableAlgorithms.map((algorithm) => (
                            <option key={algorithm.id} value={algorithm.id}>
                              {algorithm.label}{algorithm.model_available ? "" : " (no weight file)"}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="mt-3 grid gap-2 md:grid-cols-3">
                        {availableAlgorithms.map((algorithm) => (
                          <div
                            key={algorithm.id}
                            className={`rounded-lg border px-3 py-2 text-xs ${aiStatus?.algorithm === algorithm.id ? "border-[#5ba8e0] bg-white text-[#1e3a5f]" : "border-gray-200 bg-white text-gray-500"}`}
                          >
                            <div className="font-semibold">{algorithm.label}</div>
                            <div className="mt-1">{algorithm.model_available ? "มี model พร้อมรัน" : "จะ fallback ถ้าไม่มี model"}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                    {!isAdmin && (
                      <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-700">
                        บัญชีนี้เปิดดูสถานะกล้อง, AI และแยกจราจรได้ แต่การสลับโหมด, เปลี่ยนโมเดล, manual override และการบันทึก phase ต้องใช้บัญชีเจ้าหน้าที่
                      </div>
                    )}
                  </div>
                </div>

                <div className="grid gap-5 xl:grid-cols-2">
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <div className="flex items-center justify-between gap-3 mb-4">
                      <div>
                        <h2 className="text-lg font-bold text-[#1e3a5f]">AI ตัดสินใจล่าสุด</h2>
                        <p className="text-xs text-gray-400 mt-1">ข้อมูลสดจาก runtime ขณะโหมด AI ทำงาน</p>
                      </div>
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">
                        {liveAIDecisions.length} รายการ
                      </span>
                    </div>
                    <div className="space-y-3">
                      {liveAIDecisions.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-sm text-gray-400">
                          ยังไม่มี decision สดจาก AI ในรอบล่าสุด
                        </div>
                      ) : (
                        liveAIDecisions.slice(0, 6).map((decision) => (
                          <div key={`${decision.junction_id}-${decision.timestamp}`} className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="text-sm font-semibold text-[#1e3a5f]">{getJunctionNameById(decision.junction_id)}</div>
                                <div className="mt-1 text-xs text-gray-500">
                                  Phase {decision.current_phase ?? 0} → {decision.phase} • {decision.algorithm || aiStatus?.algorithm || "AI"}
                                </div>
                              </div>
                              <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${decision.applied ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                                {decision.applied ? "applied" : "pending"}
                              </span>
                            </div>
                            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 md:grid-cols-4">
                              <div>คิว: {decision.queue_length ?? 0}</div>
                              <div>รอสะสม: {decision.waiting_time ?? 0}</div>
                              <div>รถ: {decision.cars ?? 0}</div>
                              <div>ความเร็ว: {decision.avg_speed_kmh ?? 0}</div>
                            </div>
                            <div className="mt-2 text-[11px] text-gray-400">{formatDecisionTimestamp(decision.timestamp)}</div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <div className="flex items-center justify-between gap-3 mb-4">
                      <div>
                        <h2 className="text-lg font-bold text-[#1e3a5f]">ประวัติ AI จากฐานข้อมูล</h2>
                        <p className="text-xs text-gray-400 mt-1">อ่านจากตาราง ai_decisions สำหรับเทียบผลแต่ละโมเดล</p>
                      </div>
                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">
                        {recentDbDecisions.length} รายการ
                      </span>
                    </div>
                    <div className="space-y-3">
                      {recentDbDecisions.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-sm text-gray-400">
                          ยังไม่มีข้อมูลใน ai_decisions
                        </div>
                      ) : (
                        recentDbDecisions.map((entry) => {
                          const output = entry.output || {};
                          const phase = Number(output.phase ?? output.target_phase ?? 0);
                          const applied = Boolean(output.applied ?? false);
                          const queueLength = Number(entry.input_data.queue_length ?? 0);
                          const waitingTime = Number(entry.input_data.waiting_time ?? 0);
                          return (
                            <div key={entry.id} className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <div className="text-sm font-semibold text-[#1e3a5f]">{getJunctionNameById(entry.junction_id)}</div>
                                  <div className="mt-1 text-xs text-gray-500">
                                    {entry.model_version || "unknown"} • phase {phase}
                                  </div>
                                </div>
                                <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${applied ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>
                                  {applied ? "applied" : "logged"}
                                </span>
                              </div>
                              <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 md:grid-cols-4">
                                <div>คิว: {queueLength}</div>
                                <div>รอสะสม: {waitingTime}</div>
                                <div>reward: {entry.reward.toFixed(2)}</div>
                                <div>รถ: {Number(entry.input_data.cars ?? 0)}</div>
                              </div>
                              <div className="mt-2 text-[11px] text-gray-400">{formatDecisionTimestamp(entry.timestamp)}</div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  </div>
                </div>

                {/* Quick Signal Override */}
                {selectedSignalReady && selectedJunction && (
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

                    <div className="mb-4 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-700">
                      กล้องที่เลือกแทน &quot;แยก&quot; ไม่ได้แทน &quot;ทิศเดียว&quot; เสมอไป จึงยังมีตัวเลือกคุมทั้งแยกหรือคุมเฉพาะแนวเหนือ-ใต้ / ตะวันออก-ตก สำหรับงานทดลองสัญญาณไฟ
                    </div>

                    <div className="mb-4 grid gap-2 md:grid-cols-3">
                      {manualDirectionOptions.map((option) => (
                        <button
                          key={option.value}
                           onClick={() => setSelectedDirection(option.value)}
                           disabled={aiStatus?.mode === "ai" || !isAdmin}
                           className={`rounded-lg border px-3 py-2 text-left transition-colors ${selectedDirection === option.value ? "border-[#5ba8e0] bg-blue-50" : "border-gray-200 bg-white hover:bg-gray-50"} disabled:opacity-40`}
                        >
                          <div className="text-sm font-semibold text-[#1e3a5f]">{option.label}</div>
                          <div className="mt-1 text-[11px] text-gray-500">{option.hint}</div>
                        </button>
                      ))}
                    </div>

                    {/* Quick override buttons */}
                    <div className="flex gap-3">
                      {(
                        [
                          ["red", "#ef4444", "แดง"],
                          ["yellow", "#eab308", "เหลือง"],
                          ["green", "#22c55e", "เขียว"],
                        ] as const
                      ).map(([state, color, label]) => (
                        <button
                           key={state}
                           onClick={() => handleSignal(state)}
                           disabled={aiStatus?.mode === "ai" || !isAdmin}
                           className="flex-1 flex flex-col items-center gap-2 py-3 rounded-lg border-2 transition-all hover:shadow-md disabled:opacity-30 disabled:cursor-not-allowed"
                          style={{ borderColor: color }}
                        >
                          <div className="w-8 h-8 rounded-full" style={{ backgroundColor: color }} />
                          <span className="text-xs font-medium text-gray-600">{label}</span>
                        </button>
                      ))}
                    </div>
                    {(aiStatus?.mode === "ai" || !isAdmin) && (
                      <p className="text-xs text-amber-500 mt-2">
                        {aiStatus?.mode === "ai"
                          ? "* ปิดโหมด AI ก่อนเพื่อควบคุมเอง"
                          : "* สิทธิ์ manual ต้องใช้บัญชีเจ้าหน้าที่"}
                      </p>
                    )}
                  </div>
                )}

                {!selectedSignalReady && selectedJunction && (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    กล้องนี้มีในชุด CCTV แล้ว แต่ runtime สัญญาณไฟยังไม่ได้ส่งข้อมูล junction สำหรับควบคุม จึงแสดงสตรีมได้ก่อนและซ่อนปุ่มสลับไฟไว้
                  </div>
                )}

                {/* Phase Timing Editor */}
                {selectedJunction && selectedJunction.phases.length > 0 && (
                  <div className="bg-white rounded-xl border border-gray-200 p-6">
                    <h2 className="text-lg font-bold text-[#1e3a5f] mb-2">ตั้งเวลาสีไฟ</h2>
                    <p className="text-xs text-gray-400 mb-4">
                      แสดงเป็นสีหลัก เขียว / เหลือง / แดง และปรับเวลาเป็นวินาที
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

                    {/* Simple grouped timing controls */}
                    <div className="grid gap-3 md:grid-cols-3">
                      {([
                        { type: "green", label: "เขียว", color: "#22c55e", min: 5, max: 120, step: 5 },
                        { type: "yellow", label: "เหลือง", color: "#eab308", min: 3, max: 15, step: 1 },
                        { type: "red", label: "แดง", color: "#ef4444", min: 3, max: 60, step: 1 },
                      ] as const).map((option) => {
                        const matchingPhases = selectedJunction.phases.filter((phase) => phase.type === option.type);
                        if (matchingPhases.length === 0) return null;
                        const value = Math.round(
                          matchingPhases.reduce((sum, phase) => sum + (editDurations[phase.index] ?? phase.duration), 0) / matchingPhases.length
                        );
                        const applyValue = (nextValue: number) => {
                          const clamped = Math.max(option.min, Math.min(option.max, nextValue));
                          setEditDurations((prev) => {
                            const next = { ...prev };
                            matchingPhases.forEach((phase) => {
                              next[phase.index] = clamped;
                            });
                            return next;
                          });
                        };
                        return (
                          <div key={option.type} className="rounded-lg border border-gray-200 bg-white p-4">
                            <div className="mb-3 flex items-center gap-2">
                              <span className="h-4 w-4 rounded-full" style={{ backgroundColor: option.color }} />
                              <span className="text-sm font-semibold text-[#1e3a5f]">ไฟ{option.label}</span>
                              <span className="ml-auto text-[10px] text-gray-400">{matchingPhases.length} เฟส</span>
                            </div>
                            <div className="flex items-center justify-between gap-2">
                              <button
                                type="button"
                                onClick={() => applyValue(value - option.step)}
                                disabled={aiStatus?.mode === "ai" || !isAdmin}
                                className="h-9 w-9 rounded-md bg-gray-100 text-lg font-semibold text-gray-600 hover:bg-gray-200 disabled:opacity-30"
                              >
                                -
                              </button>
                              <label className="min-w-0 flex-1 text-center">
                                <input
                                  type="number"
                                  min={option.min}
                                  max={option.max}
                                  value={value}
                                  onChange={(e) => applyValue(parseInt(e.target.value) || option.min)}
                                  disabled={aiStatus?.mode === "ai" || !isAdmin}
                                  className="w-full rounded-md border border-gray-300 py-1.5 text-center text-lg font-semibold text-gray-700 disabled:opacity-30"
                                />
                                <span className="mt-1 block text-[10px] text-gray-400">วินาที</span>
                              </label>
                              <button
                                type="button"
                                onClick={() => applyValue(value + option.step)}
                                disabled={aiStatus?.mode === "ai" || !isAdmin}
                                className="h-9 w-9 rounded-md bg-gray-100 text-lg font-semibold text-gray-600 hover:bg-gray-200 disabled:opacity-30"
                              >
                                +
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {/* Save button */}
                    <div className="mt-4 flex items-center gap-3">
                      <button
                         onClick={handleSavePhases}
                         disabled={aiStatus?.mode === "ai" || saving || !isAdmin}
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

                <div className="bg-white rounded-xl border border-gray-200 p-6">
                  <div className="flex items-center justify-between gap-3 mb-4">
                      <div>
                        <h2 className="text-lg font-bold text-[#1e3a5f]">กิจกรรมควบคุมล่าสุด</h2>
                        <p className="text-xs text-gray-400 mt-1">สรุปจาก runtime ปัจจุบันและ AI decision ล่าสุด โดยไม่พึ่ง audit table ที่ไม่จำเป็น</p>
                      </div>
                    </div>
                    <div className="space-y-2">
                      {recentControlLogs.length === 0 ? (
                        <div className="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-sm text-gray-400">
                          ยังไม่มีกิจกรรม runtime ให้แสดง
                        </div>
                      ) : (
                      recentControlLogs.map((log) => (
                        <div key={`${log.timestamp}-${log.message}`} className="flex items-start justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                          <div>
                            <div className="text-sm text-gray-700">{log.message}</div>
                            <div className="mt-1 text-[11px] text-gray-400">{formatDecisionTimestamp(log.timestamp)}</div>
                          </div>
                          <span className={`rounded-full px-2 py-1 text-[11px] font-semibold ${log.level === "error" ? "bg-red-100 text-red-700" : log.level === "warning" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-600"}`}>
                            {log.level}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
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
