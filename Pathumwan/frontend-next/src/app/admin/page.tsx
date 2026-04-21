"use client";

import { useEffect, useState } from "react";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import api, { getCameraRuntimeStatuses } from "@/lib/api";
import type { CameraRuntimeStatus, User } from "@/lib/types";
import { Shield, UserIcon } from "lucide-react";

interface LogEntry {
  timestamp: string;
  message: string;
  level: string;
}

export default function AdminPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [cameraRuntime, setCameraRuntime] = useState<CameraRuntimeStatus[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchData() {
      try {
        const [usersRes, logsRes] = await Promise.allSettled([
          api.get<User[]>("/admin/users"),
          api.get<LogEntry[]>("/admin/logs"),
        ]);
        if (usersRes.status === "fulfilled") setUsers(usersRes.value.data);
        if (logsRes.status === "fulfilled") setLogs(logsRes.value.data);
        const runtime = await getCameraRuntimeStatuses().catch(() => []);
        setCameraRuntime(runtime);
      } catch {
        /* offline */
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  return (
    <ProtectedRoute adminOnly>
      <div className="min-h-screen bg-slate-900">
        <Navbar />
        <div className="pt-14 p-6">
          <h1 className="text-xl font-bold text-white mb-6">จัดการระบบ</h1>

          {loading ? (
            <div className="flex justify-center py-20">
              <div className="animate-spin h-8 w-8 border-2 border-cyan-400 border-t-transparent rounded-full" />
            </div>
          ) : (
            <div className="space-y-6">
              {/* User Management */}
              <div className="bg-slate-800 rounded-xl border border-slate-700 p-6">
                <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                  <UserIcon size={16} />
                  รายชื่อผู้ใช้
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-400 border-b border-slate-700">
                        <th className="text-left py-2 px-3">ID</th>
                        <th className="text-left py-2 px-3">ชื่อผู้ใช้</th>
                        <th className="text-left py-2 px-3">อีเมล</th>
                        <th className="text-left py-2 px-3">บทบาท</th>
                        <th className="text-left py-2 px-3">วันที่สมัคร</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr
                          key={u.id}
                          className="border-b border-slate-700/50 hover:bg-slate-700/30"
                        >
                          <td className="py-2 px-3 text-slate-400 font-mono">
                            {u.id}
                          </td>
                          <td className="py-2 px-3 text-white">{u.username}</td>
                          <td className="py-2 px-3 text-slate-300">
                            {u.email}
                          </td>
                          <td className="py-2 px-3">
                            <span
                              className={`text-xs px-2 py-0.5 rounded-full ${
                                u.role === "admin"
                                  ? "bg-cyan-500/20 text-cyan-400"
                                  : "bg-slate-600/50 text-slate-300"
                              }`}
                            >
                              {u.role === "admin" ? "แอดมิน" : "ผู้ใช้"}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-slate-400 text-xs">
                            {u.created_at
                              ? new Date(u.created_at).toLocaleDateString(
                                  "th-TH"
                                )
                              : "—"}
                          </td>
                        </tr>
                      ))}
                      {users.length === 0 && (
                        <tr>
                          <td
                            colSpan={5}
                            className="py-8 text-center text-slate-500"
                          >
                            ไม่มีข้อมูลผู้ใช้
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* System Logs */}
              <div className="bg-slate-800 rounded-xl border border-slate-700 p-6">
                <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                  <Shield size={16} />
                  บันทึกระบบ
                </h2>
                <div className="max-h-80 overflow-y-auto space-y-1 font-mono text-xs">
                  {logs.length === 0 ? (
                    <p className="text-slate-500 font-sans text-sm">
                      ไม่มีบันทึก
                    </p>
                  ) : (
                    logs.map((log, i) => (
                      <div
                        key={i}
                        className="flex gap-3 py-1 border-b border-slate-700/30"
                      >
                        <span className="text-slate-500 shrink-0">
                          {new Date(log.timestamp).toLocaleString("th-TH")}
                        </span>
                        <span
                          className={`shrink-0 ${
                            log.level === "error"
                              ? "text-red-400"
                              : log.level === "warning"
                                ? "text-yellow-400"
                                : "text-slate-400"
                          }`}
                        >
                          [{log.level}]
                        </span>
                        <span className="text-slate-300">{log.message}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="bg-slate-800 rounded-xl border border-slate-700 p-6">
                <h2 className="text-sm font-semibold text-slate-300 mb-4">Camera Runtime Status</h2>
                <div className="mb-4 grid gap-3 md:grid-cols-4">
                  <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                    <div className="text-[11px] text-slate-400">กล้องทั้งหมด</div>
                    <div className="mt-1 text-2xl font-semibold text-white">{cameraRuntime.length}</div>
                  </div>
                  <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                    <div className="text-[11px] text-slate-400">Calibration พร้อม</div>
                    <div className="mt-1 text-2xl font-semibold text-emerald-400">{cameraRuntime.filter((item) => item.calibration_ready).length}</div>
                  </div>
                  <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                    <div className="text-[11px] text-slate-400">มี Zone config</div>
                    <div className="mt-1 text-2xl font-semibold text-cyan-400">{cameraRuntime.filter((item) => item.zone_count > 0).length}</div>
                  </div>
                  <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                    <div className="text-[11px] text-slate-400">Tracking ready</div>
                    <div className="mt-1 text-2xl font-semibold text-amber-400">{cameraRuntime.filter((item) => item.tracking_ready).length}</div>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-slate-400 border-b border-slate-700">
                        <th className="text-left py-2 px-3">Camera</th>
                        <th className="text-left py-2 px-3">Road/Junction</th>
                        <th className="text-left py-2 px-3">Calibration</th>
                        <th className="text-left py-2 px-3">Zones</th>
                        <th className="text-left py-2 px-3">Tracking</th>
                        <th className="text-left py-2 px-3">Metrics</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cameraRuntime.map((item) => (
                        <tr key={item.camera_id} className="border-b border-slate-700/50 hover:bg-slate-700/30 align-top">
                          <td className="py-2 px-3">
                            <div className="font-medium text-white">{item.camera_name}</div>
                            <div className="text-[11px] text-slate-400 font-mono">{item.camera_id}</div>
                          </td>
                          <td className="py-2 px-3 text-slate-300">
                            <div>{item.road_id || "—"}</div>
                            <div className="text-[11px] text-slate-500">{item.junction_id || "—"}</div>
                          </td>
                          <td className="py-2 px-3">
                            <span className={`text-xs px-2 py-0.5 rounded-full ${item.calibration_ready ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-600/40 text-slate-300"}`}>
                              {item.calibration_ready ? "พร้อม" : "ยังไม่พร้อม"}
                            </span>
                            {item.calibration && (
                              <div className="mt-1 text-[11px] text-slate-500">
                                {item.calibration.image_width}x{item.calibration.image_height} / {item.calibration.pixels_per_meter || 0} px/m
                              </div>
                            )}
                          </td>
                          <td className="py-2 px-3 text-slate-300">
                            <div>{item.enabled_zone_count}/{item.zone_count}</div>
                            <div className="mt-1 text-[11px] text-slate-500">{item.zone_types.length ? item.zone_types.join(", ") : "—"}</div>
                          </td>
                          <td className="py-2 px-3">
                            <span className={`text-xs px-2 py-0.5 rounded-full ${item.tracking_ready ? "bg-amber-500/20 text-amber-300" : "bg-slate-600/40 text-slate-300"}`}>
                              {item.tracking_ready ? "พร้อม" : "รอ config"}
                            </span>
                          </td>
                          <td className="py-2 px-3 text-slate-300 text-[11px]">
                            {item.latest_metric_at ? new Date(item.latest_metric_at).toLocaleString("th-TH") : "ยังไม่มี metric"}
                          </td>
                        </tr>
                      ))}
                      {cameraRuntime.length === 0 && (
                        <tr>
                          <td colSpan={6} className="py-8 text-center text-slate-500">ไม่มีข้อมูล camera runtime</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
